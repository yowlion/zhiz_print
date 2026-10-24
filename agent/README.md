# ZhizPrintAgent(方案A · 本机打印代理)

zhiz_print「扫码静默打印 · 多路线」的 Windows 打印代理(方案A,总方案见
`.claude/plan/SCAN_PRINT_MULTIROUTE_PLAN.md` §2)。Go 单静态 exe
(`GOOS=windows GOARCH=amd64 CGO_ENABLED=0`;go.mod 现要求 Go ≥ 1.26,
因唯一第三方依赖 golang.org/x/sys v0.49.0 的 go 指令,见下「构建」),
配套钉死版本的 SumatraPDF 3.6.1(3.5.0-3.5.2 有公开 RCE,禁入)。

## 交付包结构(发到现场的内容)

```
ZhizPrintAgent/
    agent.exe                  本代理(GOOS=windows GOARCH=amd64 CGO_ENABLED=0)
    SumatraPDF.exe             SumatraPDF 3.6.1 x64(钉死版本,禁止自动下载)
    SumatraPDF-settings.txt    关自动更新模板(agent.exe install 也可生成)
    install.bat                免管理员安装(%LOCALAPPDATA% + schtasks onlogon)
    uninstall.bat              卸载(删任务+杀进程)
    README.md                  本文件
```

## 构建(打包机,联网)

```
cd agent
go mod tidy                                 # 生成 go.sum(唯一第三方依赖 golang.org/x/sys)
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" \
    -o dist/agent.exe ./cmd/agent
```

模块缓存已备料的机器可加 `GOPROXY=off` 离线构建。

## 部署步骤(现场工位,免管理员)

1. 把整个交付包目录拷到工位(U盘/内网共享均可)。
2. 双击 `install.bat`:文件落到 `%LOCALAPPDATA%\ZhizPrintAgent`,并注册
   onlogon 计划任务(当前用户,`/rl limited`,不需要管理员)。
3. 首次运行 `agent.exe`(或登录后计划任务拉起)进入**首启向导**,依次填:
   `station_id` / `api_key` / `api_secret` / `erp_base_url`(打印机名、
   http_proxy 可留空走默认)。向导当场做一次带签名的 `agent_register`
   验证凭据并把服务端时钟偏移写入 config.json。
   也可事先准备好 `config.json` 直接拷入 `%LOCALAPPDATA%\ZhizPrintAgent`。
4. 运行 `agent.exe doctor` 做五步体检:配置 → SumatraPDF → 打印机枚举+纸张
   能力(A4 码必须在驱动能力清单内,方案 §11 国产驱动纸张陷阱)→ ERP 带签名
   连通 → 试打样张(归档 `%LOCALAPPDATA%\ZhizPrintAgent\samples`)。
5. 登录后 kiosk 壳自动打开扫码页(`msedge --kiosk http://127.0.0.1:17980/s/<token>`)。

## config.json 字段

| 字段 | 说明 |
|---|---|
| station_id / api_key / api_secret | 与 ERP `Zprint Station` 上的凭据一致 |
| erp_base_url | `https://...`(云端)或 `http://...`(局域网) |
| printer | Windows 打印机名(空=Windows 默认打印机) |
| paper | doctor 纸张校验用(A4/LETTER/LEGAL/A3/A5/B5,默认 A4) |
| http_proxy | 云端走代理时填;空=环境变量 |
| wedge_suffix | 扫码枪结尾键 enter/tab/none(与 Station 一致) |
| listen_addr | 本机页监听地址,默认 `127.0.0.1:17980` |
| kiosk | 是否自动拉起 Edge kiosk 壳 |
| clock_offset_sec | 服务端时钟偏移,自动刷新落盘,勿手改 |
| poll/heartbeat/print_timeout | 心跳 60s;空闲轮询指数退避 1s→30s;单次打印 120s 上限 |

## 安全边界(方案 §2)

- 扫码页只绑 `127.0.0.1`,Host/Origin 双校验 + 每次启动随机 token 进 URL
  路径;浏览器**永不持有 api_secret**(HMAC 由 agent 进程签名)。
- ERP 请求签名:`sig = HMAC_SHA256(api_secret, timestamp + "." + canonical_json)`,
  与 `zhiz_print/api/scan_print.py _canonical_body` 逐字节对齐
  (sort_keys + 紧凑分隔符 + 非 ASCII 原样,±300s 窗口按持久化偏移修正,
  401 自动重同步重放一次)。
- >2MB 的 PDF 走一次性 token URL 下载(10min TTL、单次消费),落
  `%TEMP%\zhizprint-agent`,打完即删。
- api_secret 明文存于用户 profile(NTFS 用户私有 ACL),v1 已知取舍;
  DPAPI 加密列入升级路径。

## 回执口径(对客户话术,方案 §11)

agent ack 的 `exit_code` 语义(ERP `print_channels/agent.py apply_ack`):

| exit_code | 语义 |
|---|---|
| 0 | 已成功提交 Windows 打印队列(=回执天花板,非 100% 物理出纸) |
| 2 | 打印电脑上找不到该打印机 |
| 3 | 打印机离线/错误态 |
| 4 | 打印队列(spooler)拒绝 |
| 5 | 渲染文件无法被打印引擎打开(含 sha256 校验失败) |
| 6 | 未知 agent 错误(SumatraPDF 通用失败/超时/崩溃也归此,原始码在 detail) |

## 签名 / 杀软 SOP 摘要(方案 §2,不做必翻车)

- **代码签名**:OV 硬件令牌签名(约 ¥1500-4000/年)或 Azure Trusted
  Signing($9.99/月,需海外卡)。每次发布必须签名后再分发,否则 SmartScreen
  直接拦。
- **杀软白名单双渠道**:每版都在 Microsoft Security Intelligence 提交样本
  (`https://www.microsoft.com/en-us/wdsi/filesubmission`),并注册 360 开放
  平台企业账号提交样本;客户侧有管理端的,按客户杀软管理端本地加白 SOP
  走一遍(目录 + 进程双加白)。
- **禁止行为**:任何形式的自动下载二进制(含 SumatraPDF);agent 缺
  SumatraPDF.exe 时 doctor 报错并停在该步,不静默补下载。
- Win7 存量工位不走主线(标准版仅 Win10+;Win7 需 Go1.20 冻结的项目制
  legacy 构建,方案 §2)。

## 卸载

双击 `uninstall.bat`(删计划任务+杀进程),再手动删除
`%LOCALAPPDATA%\ZhizPrintAgent`(bat 无法删除自身所在目录时留下的提示见回显)。

## 已知边界(方案 §2/§11)

- 回执天花板 = 提交 spooler;缺纸/卡纸不能经退出码感知(winspool 状态位只进
  心跳,给看板用)。
- 断网时扫码直接报「无法连上打印服务器」,不打不缓存(v1);自升级通道排 v1.1。
- 前台失焦检测依赖 kiosk 窗口标题(=`ZhizPrint Scan Station`),用户改壳或换
  浏览器后需同步改 `internal/scanpage` 的 PageTitle。
