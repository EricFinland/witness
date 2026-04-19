import { ReactCompareSlider, ReactCompareSliderImage } from "react-compare-slider";
import type { Step } from "@/lib/types";
import { api } from "@/lib/api";

interface Props {
  traceId: string;
  step: Step;
}

export function ScreenshotDiff({ traceId, step }: Props) {
  const before = step.shot_before_path ? api.blobUrl(traceId, step.shot_before_path) : null;
  const after = step.shot_after_path ? api.blobUrl(traceId, step.shot_after_path) : null;

  if (!before && !after) {
    return <EmptyState label="No screenshots for this step" />;
  }
  if (before && after) {
    return (
      <div className="p-5">
        <div className="rounded-md border border-border overflow-hidden bg-black">
          <ReactCompareSlider
            itemOne={
              <ReactCompareSliderImage
                src={before}
                alt="before"
                style={{ objectFit: "contain", background: "#000" }}
              />
            }
            itemTwo={
              <ReactCompareSliderImage
                src={after}
                alt="after"
                style={{ objectFit: "contain", background: "#000" }}
              />
            }
            position={50}
            style={{ minHeight: 420 }}
          />
        </div>
        <div className="flex justify-between mt-2 text-[10px] uppercase tracking-wider text-fg-subtle">
          <span>Before</span>
          <span>After</span>
        </div>
      </div>
    );
  }
  const url = (before || after)!;
  return (
    <div className="p-5">
      <img src={url} alt="screenshot" className="max-w-full rounded-md border border-border" />
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="p-12 text-center text-fg-muted text-sm">
      {label}
    </div>
  );
}
