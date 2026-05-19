import type { TurnLatencyMetrics, MilestoneStatus } from '../lib/latencyEvents';

interface Props {
  metrics: TurnLatencyMetrics;
}

function formatMilestone(
  status: MilestoneStatus,
  ms: number | null,
): string {
  if (status === 'received' && ms !== null) return `${ms} ms`;
  if (status === 'waiting') return 'Waiting...';
  if (status === 'not_received') return 'Not received';
  return '—';
}

export default function LatencyPanel({ metrics }: Props) {
  const showTurn = metrics.turnId || metrics.turnActive;

  return (
    <aside className="latency-panel" aria-label="Turn latency">
      <h3 className="latency-panel-title">Latency</h3>
      <dl className="latency-panel-grid">
        <div className="latency-row">
          <dt>LLM First Token</dt>
          <dd>{formatMilestone(metrics.llmStatus, metrics.llmFirstTokenMs)}</dd>
        </div>
        <div className="latency-row">
          <dt>TTS First Chunk</dt>
          <dd>{formatMilestone(metrics.ttsStatus, metrics.ttsFirstChunkMs)}</dd>
        </div>
        {showTurn && (
          <div className="latency-row latency-row--meta">
            <dt>Turn</dt>
            <dd>{metrics.turnId ?? (metrics.turnActive ? 'In progress' : '—')}</dd>
          </div>
        )}
      </dl>
    </aside>
  );
}
