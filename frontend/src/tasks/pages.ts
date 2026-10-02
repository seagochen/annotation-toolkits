import type { ComponentType } from "react";

import type { TaskType } from "../project-meta";
import { CaptionReviewPage } from "./caption/CaptionReviewPage";
import { ClassificationReviewPage } from "./classification/ClassificationReviewPage";
import { DepthReviewPage } from "./depth/DepthReviewPage";
import { DetectionReviewPage } from "./detection/DetectionReviewPage";
import { ReIDReviewPage } from "./reid/ReIDReviewPage";
import { SegmentationReviewPage } from "./segmentation/SegmentationReviewPage";

/**
 * The annotation page of each task type. Its route path comes from
 * `taskEntries` in project-meta.ts; keyed by `TaskType`, so a task type
 * added there without a page here (or the reverse) fails to type-check.
 */
export const taskPages: Readonly<Record<TaskType, ComponentType>> = {
  reid: ReIDReviewPage,
  classification: ClassificationReviewPage,
  captioning: CaptionReviewPage,
  detection: DetectionReviewPage,
  segmentation: SegmentationReviewPage,
  depth: DepthReviewPage,
};
