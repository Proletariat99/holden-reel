from pathlib import Path
import os
from uuid import UUID


_ARTIFACT_DIRECTORIES = {"preview": "previews", "final": "exports"}
_ARTIFACT_SUFFIXES = {".mp4"}


class ArtifactStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def path_for(
        self,
        project_id: UUID,
        kind: str,
        artifact_id: UUID,
        suffix: str,
    ) -> Path:
        if not isinstance(project_id, UUID) or not isinstance(artifact_id, UUID):
            raise ValueError("project and artifact IDs must be UUIDs")
        directory = _ARTIFACT_DIRECTORIES.get(kind)
        if directory is None:
            raise ValueError("artifact kind must be preview or final")
        if suffix not in _ARTIFACT_SUFFIXES:
            raise ValueError("unsupported artifact suffix")

        data_root = self.data_dir.resolve()
        project_root = self.data_dir / "projects" / str(project_id)
        output_dir = project_root / directory
        output_path = output_dir / f"{artifact_id}{suffix}"
        resolved_project_root = project_root.resolve()
        if not resolved_project_root.is_relative_to(data_root):
            raise ValueError("artifact path escapes the data directory")
        if not output_path.resolve().is_relative_to(resolved_project_root):
            raise ValueError("artifact path escapes its project directory")
        output_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(project_root, 0o700)
        os.chmod(output_dir, 0o700)
        return output_path
