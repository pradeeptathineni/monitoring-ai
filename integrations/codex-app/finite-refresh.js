// Run inside the authorized assistant tool runtime, not as a standalone account client
async function refreshAppInventory({ projectRoot, output, samples = 3 }) {
  if (!Number.isInteger(samples) || samples < 1 || samples > 3) throw new Error("Use one to three finite samples");
  const quote = (value) => "'" + value.replace(/'/g, "'\\''") + "'";
  const observations = [];
  for (let index = 0; index < samples; index++) {
    if (index) await new Promise((resolve) => setTimeout(resolve, 10000));
    const reply = await tools.mcp__codex_app__list_threads({ mode: "recent", limit: 20 });
    if (reply.isError) throw new Error("Supported app inventory unavailable; export was not refreshed");
    const native = JSON.parse(reply.content.find((item) => item.type === "text").text);
    const selected = new Map();
    for (const row of [...(native.pinnedThreads || []), ...(native.threads || [])]) {
      if (!["codex", "chatgpt"].includes(row.kind)) continue;
      const value = { id: row.id, kind: row.kind, status: row.status,
        host: row.hostId === "local" ? "local" : row.hostId ? "connected" : "unknown",
        updated_at: new Date(row.updatedAt * 1000).toISOString() };
      const key = JSON.stringify([row.kind, row.id]);
      if (selected.has(key) && JSON.stringify(selected.get(key)) !== JSON.stringify(value))
        throw new Error("Inventory repeated an identity with conflicting metadata; export was not refreshed");
      selected.set(key, value);
      if (selected.size === 100) break;
    }
    const clock = await tools.clock__curr_time({});
    const captured = new Date(clock.current_time.replace(" UTC", "Z").replace(" ", "T")).toISOString();
    // Only the five selected metadata fields cross into the capture process
    const rows = [...selected.values()];
    const command = `python3 -m monitoring_ai sources capture-app-threads --output ${quote(output)} --captured-at ${quote(captured)} --replace <<'APP_METADATA'\n${JSON.stringify(rows)}\nAPP_METADATA`;
    const published = await tools.exec_command({ cmd: command, workdir: projectRoot, max_output_tokens: 300 });
    if (published.exit_code !== 0) throw new Error("Inventory export refresh failed or is uncertain; inspect the private output");
    const receipt = JSON.parse(published.output);
    observations.push({ captured_at: receipt.captured_at, tasks: receipt.tasks });
    text({ sample: index + 1, captured_at: receipt.captured_at, tasks: receipt.tasks });
  }
  return { samples: observations.length, observations, persistent_process_installed: false };
}
