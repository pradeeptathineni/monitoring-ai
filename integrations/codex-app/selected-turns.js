// Run inside the authorized assistant tool runtime; selected IDs are never written to the export
async function captureSelectedCodexTurns({ projectRoot, output, targets, samples = 1, replace = false }) {
  if (typeof projectRoot !== "string" || !projectRoot || typeof output !== "string" || !output)
    throw new Error("Select the product checkout and a private output");
  if (!Number.isInteger(samples) || samples < 1 || samples > 3) throw new Error("Use one to three finite samples");
  if (typeof replace !== "boolean" || !Array.isArray(targets) || targets.length < 1 || targets.length > 4)
    throw new Error("Explicitly select one to four Codex tasks and an output mode");
  const seen = new Set();
  for (const target of targets) {
    if (!target || typeof target.threadId !== "string" || !target.threadId ||
      Object.keys(target).some(key => !["threadId", "hostId"].includes(key)) ||
      (target.hostId !== undefined && typeof target.hostId !== "string")) throw new Error("Unsupported selected task");
    const key = JSON.stringify([target.hostId || "local", target.threadId]);
    if (seen.has(key)) throw new Error("Duplicate selected task");
    seen.add(key);
  }
  const quote = value => "'" + value.replace(/'/g, "'\\''") + "'";
  const iso = value => {
    if (value === null || value === undefined) return null;
    if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("Unsupported source timestamp");
    return new Date(value * 1000).toISOString();
  };
  const clock = async () => new Date((await tools.clock__curr_time({})).current_time.replace(" UTC", "Z").replace(" ", "T")).toISOString();
  for (let sample = 0; sample < samples; sample++) {
    if (sample) await new Promise(resolve => setTimeout(resolve, 10000));
    const selected = [];
    for (const target of targets) {
      const reply = await tools.mcp__codex_app__read_thread({ ...target, turnLimit: 4, includeOutputs: false, maxOutputCharsPerItem: 1 });
      if (reply.isError) throw new Error("Supported selected-task read failed; export was not refreshed");
      const value = reply.structuredContent || JSON.parse(reply.content.find(item => item.type === "text").text);
      if (value.schemaVersion !== 1 || value.thread?.kind !== "codex" || value.thread.id !== target.threadId ||
        (target.hostId !== undefined && target.hostId !== value.thread.hostId) ||
        !Array.isArray(value.turns) || value.turns.length > 4 || typeof value.page?.hasMore !== "boolean" || value.page.order !== "newest_first")
        throw new Error("Unsupported selected-task metadata; export was not refreshed");
      if (value.turns.some(turn => !["error", "startedAt", "completedAt", "durationMs"].every(key => Object.hasOwn(turn, key))))
        throw new Error("Selected turn omitted supported metadata fields; export was not refreshed");
      selected.push({ id: value.thread.id, host: value.thread.hostId === "local" ? "local" : value.thread.hostId ? "connected" : "unknown",
        updated_at: iso(value.thread.updatedAt), observed_at: await clock(), has_more: value.page.hasMore,
        turns: value.turns.map(turn => ({ id: turn.id, status: turn.status, started_at: iso(turn.startedAt), completed_at: iso(turn.completedAt),
          duration_ms: turn.durationMs ?? null, error_present: turn.error !== null && turn.error !== undefined })) });
    }
    const captured = await clock();
    // Only explicitly selected metadata crosses into the private local capture process
    const command = `python3 -m monitoring_ai sources capture-codex-app-turns --output ${quote(output)} --captured-at ${quote(captured)} ${sample || replace ? "--replace" : ""} <<'CODEX_TURN_METADATA'\n${JSON.stringify(selected)}\nCODEX_TURN_METADATA`;
    const published = await tools.exec_command({ cmd: command, workdir: projectRoot, max_output_tokens: 300 });
    if (published.exit_code !== 0) throw new Error("Selected-turn export failed or is uncertain; inspect the private output");
    text({ sample: sample + 1, ...JSON.parse(published.output) });
  }
  return { samples, persistent_process_installed: false };
}
