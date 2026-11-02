import { notFound } from 'next/navigation';
import { loadWidgetState } from '../../../lib/load-widget-snapshot';
import WidgetPanel from '../widget-panel';
import '../widgets.css';

export const dynamic = 'force-dynamic';

export default async function WidgetPage({ params }: { params: Promise<{ panel: string }> }) {
  const { panel } = await params;
  if (!['remaining', 'overview', 'allocation', 'axis'].includes(panel)) notFound();
  const state = await loadWidgetState();
  return <WidgetPanel panel={panel} initialSnapshot={state.snapshot} initialNow={state.requestedAt} />;
}
