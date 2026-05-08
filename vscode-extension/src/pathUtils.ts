import * as path from 'path';

/**
 * Resolves inputPath to an absolute path.
 * - Absolute paths are returned as-is (normalized).
 * - Relative paths are resolved against workspaceRoot.
 * - Empty / undefined input returns undefined.
 */
export function resolveWorkspacePath(
  workspaceRoot: string,
  inputPath: string | null | undefined,
): string | undefined {
  if (!inputPath || inputPath.trim() === '') {
    return undefined;
  }
  const trimmed = inputPath.trim();
  if (path.isAbsolute(trimmed)) {
    return path.normalize(trimmed);
  }
  return path.resolve(workspaceRoot, trimmed);
}
