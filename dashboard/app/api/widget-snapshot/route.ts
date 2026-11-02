import { loadWidgetSnapshot } from '../../../lib/load-widget-snapshot';

export const dynamic = 'force-dynamic';

export async function GET() {
  const snapshot = await loadWidgetSnapshot();
  return Response.json(snapshot ?? { error: '快照暂不可用' }, {
    status: snapshot ? 200 : 503,
    headers: { 'Cache-Control': 'no-store, max-age=0' },
  });
}
