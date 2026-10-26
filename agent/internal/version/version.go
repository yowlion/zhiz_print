// Package version holds the single source of the agent version. It is
// reported in agent_register / agent_heartbeat and compared against the
// server-side min_agent_version (Zprint Setting).
package version

// Version of ZhizPrintAgent (v1 scope per plan 2; self-upgrade lands v1.1).
const Version = "1.0.1"
