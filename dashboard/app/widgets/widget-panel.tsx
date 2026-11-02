'use client';

import { useEffect, useState } from 'react';
import { AllocationChart, DayAxis, SleepDetail, TimeValue, compactMinutes } from '../../lib/dashboard-ui';
import { TimeMirrorSnapshot, formatDateLabel, formatTime, isTimeMirrorSnapshot } from '../../lib/snapshot';

const SOURCE_NAMES: Record<string, string> = {
  calendar: '日历', kindle: 'Kindle', computer: '电脑', phone: '手机', sleep: '睡眠',
  gadgetbridge: '手环', health: '手环', activitywatch: '电脑', activitywatch_android: '手机', koreader: 'Kindle',
};

export default function WidgetPanel({ panel, initialSnapshot, initialNow }: {
  panel: string; initialSnapshot: TimeMirrorSnapshot | null; initialNow: number;
}) {
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [offline, setOffline] = useState(!initialSnapshot);
  const [now, setNow] = useState(initialNow);

  useEffect(() => {
    // 桌面文档不输出 meta 刷新，仅更新快照，避免整页闪烁。
    let active = true;
    let controller: AbortController | null = null;
    const update = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 6000);
      try {
        const response = await fetch('/api/widget-snapshot', { cache: 'no-store', signal: controller.signal });
        const value: unknown = response.ok ? await response.json() : null;
        if (!isTimeMirrorSnapshot(value)) throw new Error('快照暂不可用');
        if (active) { setSnapshot(value); setOffline(false); }
      } catch {
        if (active) setOffline(true);
      } finally {
        clearTimeout(timeout);
        if (active) setNow(Date.now());
      }
    };
    void update();
    const timer = setInterval(() => { void update(); }, 30000);
    return () => { active = false; clearInterval(timer); controller?.abort(); };
  }, []);

  const obsolete = Boolean(snapshot && now !== null && now >= Date.parse(snapshot.dayEnd));
  return <main className={`desktop-widget desktop-widget--${panel}`} data-generated-at={snapshot?.generatedAt ?? ''}>
    {snapshot
      ? <><WidgetContent panel={panel} snapshot={snapshot} obsolete={obsolete} />
          <WidgetStatus snapshot={snapshot} now={now} offline={offline} obsolete={obsolete} /></>
      : <><p className="widget-eyebrow">TIME MIRROR</p><strong className="widget-empty">本机快照暂不可用</strong>
          <p className="widget-muted">等待本机服务 · 自动重试</p></>}
  </main>;
}

function WidgetContent({ panel, snapshot, obsolete }: { panel: string; snapshot: TimeMirrorSnapshot; obsolete: boolean }) {
  const title = obsolete ? `${snapshot.statisticalDate} · 上次记录` : '今天';
  if (panel === 'remaining') return <>
    <p className="widget-eyebrow">{obsolete ? title : formatDateLabel(snapshot.statisticalDate)}</p>
    <h1 className="widget-title">{obsolete ? '上次可安排时间' : '剩余自由支配时间'}</h1>
    <TimeValue minutes={snapshot.remaining.totalMinutes} />
    <div className="widget-progress"><i style={{ width: `${Math.min(100, snapshot.coverage.elapsedMinutes / 1440 * 100)}%` }} /></div>
    <p className="widget-muted">已过 {compactMinutes(snapshot.coverage.elapsedMinutes)} · {snapshot.allocation ? '预留' : '未定位预留'} {compactMinutes(snapshot.allocation?.futureReservedMinutes ?? snapshot.remaining.unplacedReserveMinutes)}</p>
  </>;
  if (panel === 'overview') return <Overview snapshot={snapshot} title={title} />;
  if (panel === 'allocation') return <>
    <h1 className="widget-title">{title}的时间分配 <span>24h</span></h1>
    {snapshot.allocation
      ? <AllocationChart allocation={snapshot.allocation} elapsedMinutes={snapshot.coverage.elapsedMinutes} />
      : <p className="widget-empty">时间分配缺记录</p>}
  </>;
  return <>
    <h1 className="widget-title">{title}的轨迹 <span>00:00 — 24:00</span></h1>
    <DayAxis snapshot={snapshot} />
  </>;
}

function Overview({ snapshot, title }: { snapshot: TimeMirrorSnapshot; title: string }) {
  const sleep = snapshot.sleepSummary;
  const validSleep = sleep && !sleep.estimated && sleep.startAt && sleep.endAt && sleep.intervals.length > 0;
  const study = snapshot.allocation?.categories.find((item) => item.id === 'study');
  const entertainment = snapshot.allocation?.categories.find((item) => item.id === 'entertainment');
  return <>
    <h1 className="widget-title">睡眠 · {title}概况</h1>
    <div className="widget-sleep-value">{validSleep ? `寝起 ${compactMinutes(sleep.minutes)}` : '暂无睡眠记录'}</div>
    {validSleep ? <SleepDetail sleep={sleep} /> : <p className="widget-muted">等待手环记录</p>}
    <div className="widget-totals"><span>学习 <b>{study ? compactMinutes(study.minutes) : '缺记录'}</b></span>
      <span>娱乐 <b>{entertainment ? compactMinutes(entertainment.minutes) : '缺记录'}</b></span></div>
  </>;
}

function WidgetStatus({ snapshot, now, offline, obsolete }: {
  snapshot: TimeMirrorSnapshot; now: number | null; offline: boolean; obsolete: boolean;
}) {
  const stale = now !== null && now - Date.parse(snapshot.generatedAt) > 12 * 60000;
  const issues = Object.entries(snapshot.sources).filter(([, source]) => source.status !== 'ok').map(([name, source]) => {
    const label = SOURCE_NAMES[name] ?? '来源';
    if (source.errorCode === 'awaiting_today_sync') return `${label}待同步`;
    return `${label}${source.status === 'cached' ? '缓存' : source.status === 'error' ? '异常' : '缺记录'}`;
  });
  const details = [obsolete ? '跨日旧记录' : '', offline ? '离线缓存' : '', stale ? '快照已过期' : '',
    ...issues, snapshot.overallStatus !== 'ok' && !issues.length ? '部分数据缺失' : ''].filter(Boolean);
  return <footer className={`widget-status ${details.length ? 'widget-status--partial' : ''}`} title={details.join(' · ') || '快照读取正常'}>
    <span>更新 {snapshot.statisticalDate.slice(5).replace('-', '/')} {formatTime(snapshot.generatedAt)}</span>
    <span>{details.length ? details.join(' · ') : '本机同步'}</span>
  </footer>;
}
