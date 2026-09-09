export type ShortcutBinding = Readonly<{
  key: string;
  description: string;
  onTrigger: () => void;
}>;

export type ShortcutResolution = Readonly<{
  bindings: ReadonlyMap<string, ShortcutBinding>;
  conflicts: readonly string[];
}>;

export function normalizeShortcutKey(key: string): string {
  return key.trim().toLowerCase();
}

export function resolveShortcuts(
  builtInKeys: readonly string[],
  custom: readonly ShortcutBinding[],
): ShortcutResolution {
  const reserved = new Set(builtInKeys.map(normalizeShortcutKey));
  const bindings = new Map<string, ShortcutBinding>();
  const conflicts: string[] = [];
  for (const binding of custom) {
    const key = normalizeShortcutKey(binding.key);
    if (!key || reserved.has(key) || bindings.has(key)) {
      conflicts.push(binding.key);
      continue;
    }
    bindings.set(key, binding);
  }
  return { bindings, conflicts };
}
