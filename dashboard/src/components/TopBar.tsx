import { type ReactNode, useEffect, useState } from "react";
import { duration } from "../lib/format";
import type { RunState } from "../types";
import type { Link } from "../lib/useJournal";
import { MULTIPLIER_PRESETS } from "../lib/useMultiplier";
import { BotPicker } from "./BotPicker";
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
  runId: string | null;
  link: Link;
  state: RunState | null;
  multiplier: number;
  onMultiplier: (n: number) => void;
  rewardFactor: number;
  controls?: ReactNode; // the bot's buttons (<Controls>)
}

export function TopBar({
  runId,
  link,
  state,
  multiplier,
  onMultiplier,
  rewardFactor,
  controls,
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
      <BotPicker runId={runId} />
      {link !== "live" && (
        <span className="badge halted" data-help="bar:link">
          ● {link === "connecting" ? "connecting to bot…" : "bot offline · reconnecting"}
        </span>
      )}
      <span className={`badge ${status}`} data-help="bar:status">
        <Flash value={state?.updated_at ?? null}>
          <span className={status === "running" ? "pulse" : undefined}>●</span>
        </Flash>{" "}
        {status}
      </span>
      {status === "running" && link === "live" && (
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
      {controls}
      <span className="spacer" />
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
