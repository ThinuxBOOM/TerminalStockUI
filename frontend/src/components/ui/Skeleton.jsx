import React from "react";
import SkeletonBase, { SkeletonLine } from "../Skeleton";

// Re-export: keeps the existing Skeleton contract (label/lines/variant)
// available under the new `ui/` import path without forking behavior.
function Skeleton(props) {
  return <SkeletonBase {...props} />;
}

export { Skeleton, SkeletonLine, Skeleton as default };
