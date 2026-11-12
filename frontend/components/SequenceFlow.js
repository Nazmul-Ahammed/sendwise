"use client";
import { delayLabel } from "../lib/flow";

function findDefinition(stages, ref) {
  for (const stage of stages || []) {
    const email = (stage.emails || []).find((e) => e.ref === ref);
    if (email) return { ...email, stage: stage.stage, focus: stage.focus };
  }
  return { ref, name: ref };
}

function findCopy(emails, ref) {
  return (emails || []).find((e) => e.ref === ref) || {};
}

function Node({ type = "plain", title, sub, onClick, active }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag type={onClick ? "button" : undefined} onClick={onClick} className={`sf-node sf-${type} ${active ? "active" : ""}`}>
      <strong>{title}</strong>{sub && <span>{sub}</span>}
    </Tag>
  );
}

function EmailNode({ refId, stages, emails, onSelect, selectedRef }) {
  const def = findDefinition(stages, refId);
  const copy = findCopy(emails, refId);
  return (
    <Node type="email" title={refId} sub={copy.subject || def.name || def.focus}
      active={selectedRef === refId} onClick={onSelect ? () => onSelect(refId) : undefined} />
  );
}

function Outcome({ label, refId, stages, emails, onSelect, selectedRef, finalDelay }) {
  return (
    <div className="sf-outcome">
      <Node type="result" title={label} />
      <div className="sf-vline small" />
      <EmailNode refId={refId} stages={stages} emails={emails} onSelect={onSelect} selectedRef={selectedRef} />
      <div className="sf-vline small" />
      <Node type="delay" title={finalDelay} />
    </div>
  );
}

export default function SequenceFlow({ stages = [], emails = [], audience = "Targeted audience", onSelect, selectedRef }) {
  const firstDelay = delayLabel(stages[1]) || "3 days";
  const branchDelay = delayLabel(stages[2]) || "3 days";
  const finalDelay = delayLabel(stages[3]) || "3 days";
  return (
    <div className="sf-canvas">
      <div className="sf-tree">
        <div className="sf-top">
          <Node type="start" title="Start" />
          <div className="sf-vline" />
          <Node title="Targeted audience" sub={audience} />
          <div className="sf-vline" />
          <EmailNode refId="E1" stages={stages} emails={emails} onSelect={onSelect} selectedRef={selectedRef} />
          <div className="sf-vline" />
          <Node type="delay" title={`Wait ${firstDelay}`} />
          <div className="sf-vline" />
          <Node type="condition" title="Opened E1?" sub="Open event" />
        </div>

        <div className="sf-split first">
          <div className="sf-path">
            <Node type="result" title="Yes · Opened" />
            <div className="sf-vline" />
            <EmailNode refId="E2" stages={stages} emails={emails} onSelect={onSelect} selectedRef={selectedRef} />
            <div className="sf-vline" />
            <Node type="delay" title={`Wait ${branchDelay}`} />
            <div className="sf-vline" />
            <Node type="condition" title="E2 behavior" sub="Open + CTA click" />
            <div className="sf-outcomes">
              <Outcome label="Opened + clicked" refId="E4" finalDelay={`Wait ${finalDelay}`} {...{ stages, emails, onSelect, selectedRef }} />
              <Outcome label="Opened · no click" refId="E5" finalDelay={`Wait ${finalDelay}`} {...{ stages, emails, onSelect, selectedRef }} />
              <Outcome label="Not opened" refId="E6" finalDelay={`Wait ${finalDelay}`} {...{ stages, emails, onSelect, selectedRef }} />
            </div>
          </div>

          <div className="sf-path">
            <Node type="result" title="No · Not opened" />
            <div className="sf-vline" />
            <EmailNode refId="E3" stages={stages} emails={emails} onSelect={onSelect} selectedRef={selectedRef} />
            <div className="sf-vline" />
            <Node type="delay" title={`Wait ${branchDelay}`} />
            <div className="sf-vline" />
            <Node type="condition" title="E3 behavior" sub="Open + CTA click" />
            <div className="sf-outcomes">
              <Outcome label="Opened + clicked" refId="E7" finalDelay={`Wait ${finalDelay}`} {...{ stages, emails, onSelect, selectedRef }} />
              <Outcome label="Opened · no click" refId="E8" finalDelay={`Wait ${finalDelay}`} {...{ stages, emails, onSelect, selectedRef }} />
              <Outcome label="Not opened" refId="E9" finalDelay={`Wait ${finalDelay}`} {...{ stages, emails, onSelect, selectedRef }} />
            </div>
          </div>
        </div>

        <div className="sf-exit"><Node type="exit" title="Exit / conversion check" sub="Purchase, unsubscribe, bounce or sequence completion" /></div>
      </div>
    </div>
  );
}
