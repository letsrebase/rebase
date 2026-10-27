// Workflow kit (tooling/workflow-kit in letsrebase/rebase): refuse a Linear write that breaks
// the board's writing rules before it is sent. The rules live in one place,
// .github/kit/bin/linear_gate.py, which Claude Code calls from .claude/settings.json; this is
// omp's wiring of the same script. omp can reach an MCP tool directly or as a tool device
// (`write` to `xd://<tool>` with the arguments as JSON), so both shapes are read. Fails open:
// a gate that throws or cannot run python3 lets the call through.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const GATE = fileURLToPath(new URL("../../../.github/kit/bin/linear_gate.py", import.meta.url));

type ToolCall = { toolName?: unknown; input?: unknown };
type Block = { block: true; reason: string };
type HookApi = { on: (event: "tool_call", handler: (event: ToolCall) => Promise<Block | undefined>) => void };

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function denyReason(stdout: string): string | undefined {
	const parsed: unknown = JSON.parse(stdout);
	if (!isRecord(parsed) || !isRecord(parsed.hookSpecificOutput)) return undefined;
	const reason = parsed.hookSpecificOutput.permissionDecisionReason;
	return typeof reason === "string" ? reason : undefined;
}

export default function linearGate(pi: HookApi): void {
	pi.on("tool_call", async (event) => {
		try {
			let name = typeof event.toolName === "string" ? event.toolName : "";
			let input: unknown = event.input ?? {};
			if (name === "write" && isRecord(input) && typeof input.path === "string" && input.path.startsWith("xd://")) {
				name = input.path.slice("xd://".length);
				input = JSON.parse(typeof input.content === "string" ? input.content : "{}");
			}
			if (!/linear/i.test(name)) return undefined;
			for (const python of ["python3", "python"]) {
				const run = spawnSync(python, [GATE], {
					input: JSON.stringify({ tool_name: name, tool_input: input }),
					encoding: "utf8",
					timeout: 5000,
				});
				// The gate always exits 0; anything else is a broken interpreter (the Windows
				// Store's python3 stub), so the next one is tried.
				if (run.error || run.status !== 0) continue;
				if (!run.stdout.trim()) return undefined;
				const reason = denyReason(run.stdout);
				return reason ? { block: true, reason } : undefined;
			}
		} catch {
			// fall through: never block on a gate failure
		}
		return undefined;
	});
}
