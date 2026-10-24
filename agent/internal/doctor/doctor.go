// Package doctor implements the `agent.exe doctor` diagnostics run
// (plan 2): printer enumeration + paper capability validation, signed ERP
// ping, and a test print archived under %LOCALAPPDATA%\ZhizPrintAgent\samples.
//
// NOTE: this is the ONLY component allowed to print Chinese to the user -
// doctor runs in an interactive console and is never machine-parsed
// (plan 2: "Chinese diagnostic text, CLI only allows Chinese"). Every other
// package keeps its output ASCII English.
package doctor

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/gdzhiz/zhizprintagent/internal/config"
	"github.com/gdzhiz/zhizprintagent/internal/erpclient"
	"github.com/gdzhiz/zhizprintagent/internal/printer"
	"github.com/gdzhiz/zhizprintagent/internal/version"
)

// DMPAPER codes (wingdi.h) for the paper capability check.
var paperCodes = map[string]uint16{
	"A4":     9,
	"LETTER": 1,
	"LEGAL":  5,
	"A3":     8,
	"A5":     11,
	"B5":     13,
}

// Exit codes: 0 ok, 1 config, 2 sumatra, 3 printer, 4 erp, 5 test print.
const (
	exitOK        = 0
	exitConfig    = 1
	exitSumatra   = 2
	exitPrinter   = 3
	exitERP       = 4
	exitTestPrint = 5
)

// Run executes all checks in order; it prints progress and returns a
// process exit code (0 = all green).
func Run(cfg *config.Config) int {
	fmt.Println("ZhizPrintAgent doctor - 版本", version.Version)
	fmt.Println()

	code := exitOK

	// ---- 1. Configuration ----
	fmt.Println("== 1/5 配置检查 ==")
	if err := cfg.Validate(); err != nil {
		fmt.Printf("[FAIL] 配置不完整: %v\n", err)
		fmt.Println("       请填写 config.json(station_id / api_key / api_secret / erp_base_url),")
		fmt.Println("       或直接运行 agent.exe 进入首启向导。")
		return exitConfig
	}
	fmt.Printf("[OK]   station_id=%s\n", cfg.StationID)
	fmt.Printf("[OK]   erp_base_url=%s\n", cfg.ERPBaseURL)
	if cfg.Printer == "" {
		fmt.Println("[WARN] 未配置打印机:将打印到 Windows 默认打印机")
	} else {
		fmt.Printf("[OK]   打印机=%s\n", cfg.Printer)
	}
	fmt.Println()

	// ---- 2. SumatraPDF ----
	fmt.Println("== 2/5 SumatraPDF 检查 ==")
	exePath, err := os.Executable()
	exeDir := ""
	if err == nil {
		exeDir = filepath.Dir(exePath)
	}
	sumatra, err := printer.FindSumatra(exeDir, cfg.SumatraPath)
	if err != nil {
		fmt.Printf("[FAIL] %v\n", err)
		fmt.Println("       本代理不会自动下载二进制:请把钉死版本的 SumatraPDF 3.6.1 x64")
		fmt.Println("       (SumatraPDF.exe + SumatraPDF-settings.txt)放到 agent.exe 旁边。")
		return exitSumatra
	}
	fmt.Printf("[OK]   %s\n", sumatra)
	settingsPath := filepath.Join(filepath.Dir(sumatra), "SumatraPDF-settings.txt")
	if st, err := os.Stat(settingsPath); err == nil && !st.IsDir() {
		fmt.Printf("[OK]   %s(自动更新已关闭)\n", settingsPath)
	} else {
		fmt.Println("[WARN] SumatraPDF.exe 旁边缺少 SumatraPDF-settings.txt:")
		fmt.Println("       钉死版本必须关闭自动更新,请运行 `agent.exe install` 生成模板。")
	}
	fmt.Println()

	// ---- 3. Printer enumeration + paper capability (plan 11) ----
	fmt.Println("== 3/5 打印机枚举与纸张能力 ==")
	all, err := printer.List()
	if err != nil {
		fmt.Printf("[FAIL] 打印机枚举失败: %v\n", err)
		return exitPrinter
	}
	if len(all) == 0 {
		fmt.Println("[FAIL] 本机没有安装任何打印机")
		return exitPrinter
	}
	fmt.Printf("[OK]   共 %d 台打印机:\n", len(all))
	var target *printer.Info
	for i := range all {
		p := all[i]
		mark := "      "
		if cfg.Printer != "" && p.Name == cfg.Printer {
			mark = "  =>  "
			target = &all[i]
		}
		fmt.Printf("%s %-40s port=%-10s depth=%d status=%s%s\n",
			mark, p.Name, p.Port, p.QueueDepth, p.Status.Text(), defaultMark(p.Default))
	}
	if cfg.Printer != "" && target == nil {
		fmt.Printf("[FAIL] 配置的打印机 %q 不在上面的列表里(检查名称拼写/驱动安装)\n", cfg.Printer)
		return exitPrinter
	}
	if target != nil && !target.Status.Healthy() {
		fmt.Printf("[WARN] 目标打印机处于异常状态(%s);恢复前打印可能失败\n",
			target.Status.Text())
	}
	if err := checkPaper(cfg, target); err != nil {
		fmt.Printf("[FAIL] 纸张检查: %v\n", err)
		return exitPrinter
	}
	fmt.Println()

	// ---- 4. ERP signed ping ----
	fmt.Println("== 4/5 ERP 带签名连通 ==")
	ep, err := erpclient.New(cfg.ERPBaseURL, cfg.APIKey, cfg.APISecret, cfg.AgentID,
		version.Version, cfg.HTTPProxy, nil)
	if err != nil {
		fmt.Printf("[FAIL] %v\n", err)
		return exitERP
	}
	ep.SetClockOffset(cfg.ClockOffsetSec)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	reg, err := ep.Register(ctx, "", 0)
	if err != nil {
		fmt.Printf("[FAIL] agent_register 失败: %v\n", err)
		fmt.Println("       排查清单:erp_base_url 可达;工位已启用;api_key/api_secret 与")
		fmt.Println("       Zprint Station 一致;服务器时钟与本机相差 ±300 秒内(成功后会自动校偏)。")
		return exitERP
	}
	fmt.Printf("[OK]   agent_id=%s status=%s station_user=%s\n", reg.AgentID, reg.Status, reg.StationUser)
	fmt.Printf("[OK]   poll_interval=%ds scan_print_enabled=%v\n", reg.PollIntervalSec, reg.ScanPrintEnabled)
	if off := ep.ClockOffset(); off != cfg.ClockOffsetSec {
		fmt.Printf("[OK]   时钟偏移已刷新: %ds -> %ds(本次运行会写回 config.json)\n",
			cfg.ClockOffsetSec, off)
		cfg.ClockOffsetSec = off
		if err := cfg.Save(); err != nil {
			fmt.Printf("[WARN] 偏移落盘失败: %v\n", err)
		}
	}
	if !reg.ScanPrintEnabled {
		fmt.Println("[WARN] 该站点 scan_print_enabled=0:扫码会被拒绝,请先在 ERP 侧开启")
	}
	if reg.MinAgentVersion != "" && erpclient.VersionBelow(version.Version, reg.MinAgentVersion) {
		fmt.Printf("[WARN] 代理版本 %s 低于 min_agent_version %s:需要升级\n",
			version.Version, reg.MinAgentVersion)
	}
	fmt.Println()

	// ---- 5. Test print (archived sample, plan 2) ----
	fmt.Println("== 5/5 试打(样张归档)==")
	samplePath, err := archiveSample(sumatra)
	if err != nil {
		fmt.Printf("[WARN] 样张归档失败(继续执行): %v\n", err)
		samplePath = ""
	}
	name := "Windows 默认打印机"
	if cfg.Printer != "" {
		name = cfg.Printer
	}
	fmt.Printf("       正在打印一张 A4 样张到:%s\n", name)
	rawCode, output, err := printer.Print(sumatra, cfg.Printer, samplePath,
		time.Duration(cfg.PrintTimeoutSec)*time.Second)
	if err != nil || rawCode != 0 {
		fmt.Printf("[FAIL] 试打失败: 原始退出码=%d err=%v 输出=%s\n", rawCode, err, output)
		if samplePath != "" {
			fmt.Printf("       样张已保留供检查: %s\n", samplePath)
		}
		return exitTestPrint
	}
	fmt.Printf("[OK]   试打已提交打印队列(退出码 0)。样张: %s\n", samplePath)
	fmt.Println("       回执口径提醒:退出码 0 只代表 spooler 收单 —— 不承诺 100% 物理出纸(方案 §11)。")
	fmt.Println()

	switch code {
	case exitOK:
		fmt.Println("结论:全部通过,本工位可以上线。")
	default:
		fmt.Println("结论:存在失败项,请处理上面的 [FAIL] 后重跑。")
	}
	return code
}

func defaultMark(isDefault bool) string {
	if isDefault {
		return " [Windows 默认]"
	}
	return ""
}

// checkPaper validates the configured paper against the driver capability
// list when the printer target is known (plan 11: domestic thermal / GDI
// drivers drop A4 silently).
func checkPaper(cfg *config.Config, target *printer.Info) error {
	code, known := paperCodes[cfg.Paper]
	if !known {
		return fmt.Errorf("config 中的纸张 %q 不受支持(可用: A4, LETTER, LEGAL, A3, A5, B5)", cfg.Paper)
	}
	if target == nil {
		fmt.Printf("[WARN] 跳过纸张检查:打印到 Windows 默认打印机(paper=%s)\n", cfg.Paper)
		return nil
	}
	names, err := printer.PaperNames(target.Name, target.Port)
	if err != nil || len(names) == 0 {
		fmt.Printf("[WARN] 驱动未暴露纸张能力清单(GDI/主机驱动?);paper=%s 未能校验\n",
			cfg.Paper)
		return nil
	}
	has, err := printer.HasPaperCode(target.Name, target.Port, code)
	if err != nil {
		fmt.Printf("[WARN] 纸张能力查询失败: %v\n", err)
		return nil
	}
	if !has {
		return fmt.Errorf("纸张 %s(代码 %d)不在驱动 %q 的能力清单里", cfg.Paper, code, target.Name)
	}
	fmt.Printf("[OK]   纸张 %s(代码 %d)在驱动 %q 的能力清单里\n", cfg.Paper, code, target.Name)
	return nil
}

// archiveSample writes the built-in one-page sample PDF into
// %LOCALAPPDATA%\ZhizPrintAgent\samples (plan 2), falling back to the
// Sumatra directory when LOCALAPPDATA is unavailable.
func archiveSample(sumatraExe string) (string, error) {
	dir := ""
	if base, err := config.Dir(); err == nil {
		dir = filepath.Join(base, "samples")
	} else {
		dir = filepath.Join(filepath.Dir(sumatraExe), "samples")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	name := "sample-" + time.Now().Format("20060102-150405") + ".pdf"
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, samplePDF(), 0o600); err != nil {
		return "", err
	}
	return path, nil
}

// samplePDF builds a minimal valid one-page A4 PDF with correct xref
// offsets (no external dependency; Helvetica text only).
func samplePDF() []byte {
	content := "BT /F1 24 Tf 72 770 Td (ZhizPrintAgent sample print - A4) Tj ET"
	objects := []string{
		"<< /Type /Catalog /Pages 2 0 R >>",
		"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] " +
			"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
		fmt.Sprintf("<< /Length %d >>\nstream\n%s\nendstream", len(content), content),
		"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	}
	var buf []byte
	app := func(s string) { buf = append(buf, s...) }
	app("%PDF-1.4\n")
	offsets := make([]int, len(objects))
	for i, obj := range objects {
		offsets[i] = len(buf)
		app(fmt.Sprintf("%d 0 obj\n%s\nendobj\n", i+1, obj))
	}
	xref := len(buf)
	app(fmt.Sprintf("xref\n0 %d\n", len(objects)+1))
	app("0000000000 65535 f \n")
	for _, off := range offsets {
		app(fmt.Sprintf("%010d 00000 n \n", off))
	}
	app(fmt.Sprintf("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n",
		len(objects)+1, xref))
	return buf
}
