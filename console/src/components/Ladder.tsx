import { checkName, rungMark } from "../format";
import type { Rung } from "../types";

export function Ladder({ rungs }: { rungs: Rung[] }) {
  return (
    <ol className="ladder" aria-label="Guardrail checks in the order they ran">
      {rungs.map((rung) => {
        const { mark, tone, says } = rungMark(rung);
        return (
          <li key={rung.check} className={`rung ${rung.result}`}>
            <span className={`mark ${tone}`} aria-hidden="true">
              {mark}
            </span>
            <span className="rung-name">{checkName(rung.check)}</span>
            <span className="rung-says">{says}</span>
          </li>
        );
      })}
    </ol>
  );
}
