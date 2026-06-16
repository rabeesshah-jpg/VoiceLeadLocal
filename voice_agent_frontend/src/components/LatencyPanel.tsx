import type { TurnLatencyMetrics, MilestoneStatus } from '../lib/latencyEvents';

interface Props {
  metrics: TurnLatencyMetrics;
}

function formatMilestone(
  status: MilestoneStatus,
  ms: number | null,
): string {
  if (ms !== null) return `${ms} ms`;
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
          <dt>STT</dt>
          <dd>{formatMilestone(metrics.sttStatus, metrics.sttMs)}</dd>
        </div>
        <div className="latency-row">
          <dt>LLM (first token)</dt>
          <dd>{formatMilestone(metrics.llmStatus, metrics.llmFirstTokenMs)}</dd>
        </div>
        <div className="latency-row">
          <dt>LLM (total)</dt>
          <dd>{metrics.llmTotalMs !== null ? `${metrics.llmTotalMs} ms` : '—'}</dd>
        </div>
        <div className="latency-row">
          <dt>TTS (first audio)</dt>
          <dd>{formatMilestone(metrics.ttsStatus, metrics.ttsFirstChunkMs)}</dd>
        </div>
        <div className="latency-row">
          <dt>TTS (total)</dt>
          <dd>{metrics.ttsTotalMs !== null ? `${metrics.ttsTotalMs} ms` : '—'}</dd>
        </div>
        <div className="latency-row">
          <dt>E2E (first audio)</dt>
          <dd>{metrics.e2eFirstAudioMs !== null ? `${metrics.e2eFirstAudioMs} ms` : '—'}</dd>
        </div>
        <div className="latency-row">
          <dt>Turn total</dt>
          <dd>{metrics.totalMs !== null ? `${metrics.totalMs} ms` : '—'}</dd>
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
