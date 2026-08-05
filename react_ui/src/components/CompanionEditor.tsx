import type { Companion } from "../api/client";
import { CompanionForm } from "./CompanionForm";

interface CompanionEditorProps {
  companion: Companion;
  onSave?: (updated: Companion) => void;
  showCancel?: boolean;
  onCancel?: () => void;
}

export function CompanionEditor({
  companion,
  onSave,
  showCancel = false,
  onCancel,
}: CompanionEditorProps) {
  return (
    <CompanionForm
      mode="edit"
      companion={companion}
      showCancel={showCancel}
      onCancel={onCancel}
      onSave={onSave}
    />
  );
}

export default CompanionEditor;
