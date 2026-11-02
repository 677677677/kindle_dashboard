import { isTimeMirrorSnapshot, TimeMirrorSnapshot } from './snapshot';

// 只读现有本机快照；桌面展示层不触发采集或写入。
export async function loadWidgetSnapshot(): Promise<TimeMirrorSnapshot | null> {
  try {
    const response = await fetch('http://127.0.0.1:8765/snapshot.json', {
      cache: 'no-store', signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) return null;
    const value: unknown = await response.json();
    return isTimeMirrorSnapshot(value) ? value : null;
  } catch {
    return null;
  }
}

export async function loadWidgetState() {
  const snapshot = await loadWidgetSnapshot();
  return { snapshot, requestedAt: Date.now() };
}
