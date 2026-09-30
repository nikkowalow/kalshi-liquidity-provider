import { useEffect, useState } from "react";
import { duration } from "../lib/format";
import type { RunInfo, RunState } from "../types";
import { MULTIPLIER_PRESETS } from "../lib/useMultiplier";
import { Flash } from "./Flash";
import { Stamp } from "./Stamp";

const STALE_AFTER_S = 5;

function effectiveStatus(state: RunState | null): string {
  if (!state) return "—";
  if (
    state.status === "running" &&
    Date.now() / 1000 - state.updated_at > STALE_AFTER_S
  ) {
    return "stale";
  }
  return state.status;
}

function useClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

interface TopBarProps {
  runs: RunInfo[];
  runId: string | null;
  onChooseRun: (id: string) => void;
  follow: boolean;
  onFollow: (follow: boolean) => void;
  state: RunState | null;
  serverUp: boolean;
  multiplier: number;
  onMultiplier: (n: number) => void;
  rewardFactor: number;
}

export function TopBar({
  runs,
  runId,
  onChooseRun,
  follow,
  onFollow,
  state,
  serverUp,
  multiplier,
  onMultiplier,
  rewardFactor,
}: TopBarProps) {
  const now = useClock();
  const status = effectiveStatus(state);
  const uptime = state
    ? duration(
        (state.status === "running" ? now / 1000 : state.updated_at) -
          state.started_at,
      )
    : "";

  return (
    <div className="bar">
      <span className="brand">KLP &lt;GO&gt;</span>
      <select value={runId ?? ""} onChange={(e) => onChooseRun(e.target.value)}>
        {runs.length === 0 && (
          <option value="">no runs yet — start the bot</option>
        )}
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.id} [{r.status}]
          </option>
        ))}
      </select>
      <label data-help="bar:follow">
        <input
          type="checkbox"
          checked={follow}
          onChange={(e) => onFollow(e.target.checked)}
        />{" "}
        follow latest
      </label>
      <span className={`badge ${status}`} data-help="bar:status">
        <Flash value={state?.updated_at ?? null}>
          <span className={status === "running" ? "pulse" : undefined}>●</span>
        </Flash>{" "}
        {status}
      </span>
      {status === "running" && (
        <span className="rx" data-help="bar:rx">
          <span className="spin" />
          <Flash value={state?.updated_at ?? null}>
            <span className="led" />
          </Flash>
          RX
        </span>
      )}
      <span className={`badge ${state?.mode ?? ""}`} data-help="bar:mode">
        {state?.mode ?? "—"}
      </span>
      {state && <span>ENV {state.environment}</span>}
      {!serverUp && (
        <span className="badge halted">● dashboard server unreachable</span>
      )}
      <label className="mult" data-help="bar:multiplier">
        C ×
        <input
          type="number"
          min={0.1}
          step={1}
          list="mult-presets"
          value={multiplier}
          onChange={(e) => onMultiplier(Number(e.target.value))}
        />
        <datalist id="mult-presets">
          {MULTIPLIER_PRESETS.map((p) => (
            <option key={p} value={p} />
          ))}
        </datalist>
      </label>
      {multiplier !== 1 && (
        <span className="badge sim" data-help="bar:multiplier">
          {multiplier}× · RWRD ×{rewardFactor.toFixed(2)}{" "}
          <button type="button" onClick={() => onMultiplier(1)}>
            reset
          </button>
        </span>
      )}
      <span className="spacer" />
      {state?.session != null && (
        <span data-help="bar:session">
          SESSION {state.session}
          {state.first_started_at ? (
            <>
              {" · SINCE "}
              <Stamp ts={state.first_started_at} date />
            </>
          ) : null}
        </span>
      )}
      {uptime && <span>UP {uptime}</span>}
      <span>{new Date(now).toLocaleTimeString([], { hour12: false })}</span>
    </div>
  );
}
