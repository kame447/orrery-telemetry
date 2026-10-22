"""Same-name metadata stays attributable to the project that wrote it.

The legacy-store and alias regressions are adapted from the Issue 33 reference
implementation; they do not depend on launcher or mail-service fixtures.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dashboard import server as dashboard_server

REGISTER_LIB = pathlib.Path(__file__).resolve().parents[1] / "bin/lib/agentstack-register.sh"


@pytest.fixture
def project_paths(tmp_path):
    main = tmp_path.resolve() / "project-a"
    other = tmp_path.resolve() / "project-b"
    main.mkdir()
    other.mkdir()
    return SimpleNamespace(main=main, other=other)


def test_dashboard_legacy_metadata_is_visible_only_to_its_verified_project(
    tmp_path: pathlib.Path,
    project_paths: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "dashboard-metadata-runtime"
    binding_dir = runtime / "name-bindings"
    binding_dir.mkdir(parents=True)
    (binding_dir / "sharedcurie.json").write_text(
        json.dumps(
            {
                "project_key": str(project_paths.main),
                "agent_name": "SharedCurie",
            }
        ),
        encoding="utf-8",
    )
    annotations = runtime / "annotations.json"
    substitutions = runtime / "name-substitutions.json"
    annotations.write_text(
        json.dumps(
            {
                "SharedCurie": {
                    "role": "project-a-role",
                    "emoji": "",
                    "group": "A",
                    "project_key": str(project_paths.main),
                },
                "AmbiguousCurie": {
                    "role": "must-remain-unattributed",
                    "emoji": "",
                    "group": "legacy",
                },
            }
        ),
        encoding="utf-8",
    )
    substitutions.write_text(
        json.dumps(
            {
                "SharedCurie": {
                    "requested": "Shared-Curie",
                    "project_key": str(project_paths.main),
                    "ts": "2026-09-12T00:00:00Z",
                },
                "AmbiguousCurie": {
                    "requested": "Ambiguous-Curie",
                    "ts": "2026-09-12T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(dashboard_server, "ANNOT_PATH", str(annotations))
    monkeypatch.setattr(
        dashboard_server, "LEGACY_ANNOT_PATH", str(tmp_path / "absent-annotations.json")
    )
    monkeypatch.setattr(dashboard_server, "SUBST_PATH", str(substitutions))
    dashboard_server._ANNOT_CACHE.update(path="", mtime=-1.0, data={})
    dashboard_server._SUBST_CACHE.update(mtime=-1.0, data={})

    monkeypatch.setattr(dashboard_server, "_project_key", lambda: str(project_paths.other))
    assert "SharedCurie" not in dashboard_server._annotations()
    assert "SharedCurie" not in dashboard_server._name_substitutions()
    assert "AmbiguousCurie" not in dashboard_server._annotations()
    assert "AmbiguousCurie" not in dashboard_server._name_substitutions()

    monkeypatch.setattr(dashboard_server, "_project_key", lambda: str(project_paths.main))
    dashboard_server._ANNOT_CACHE.update(path="", mtime=-1.0, data={})
    dashboard_server._SUBST_CACHE.update(mtime=-1.0, data={})
    assert dashboard_server._annotations()["SharedCurie"]["role"] == "project-a-role"
    assert dashboard_server._name_substitutions()["SharedCurie"] == "Shared-Curie"
    # Even a current binding is not provenance for historical unkeyed data.
    assert "AmbiguousCurie" not in dashboard_server._annotations()
    assert "AmbiguousCurie" not in dashboard_server._name_substitutions()


def test_dashboard_metadata_reads_alias_equivalent_scoped_buckets_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = tmp_path / "metadata-physical"
    alias = tmp_path / "metadata-alias"
    distinct = tmp_path / "metadata-distinct"
    physical.mkdir()
    distinct.mkdir()
    alias.symlink_to(physical, target_is_directory=True)
    annotations = tmp_path / "alias-annotations.json"
    substitutions = tmp_path / "alias-substitutions.json"
    annotations.write_text(
        json.dumps(
            {
                "projects": {
                    str(alias): {
                        "agents": {
                            "LegacyAliasCurie": {
                                "role": "legacy-alias",
                                "project_key": str(alias),
                            },
                            "UnkeyedCurie": {"role": "must-remain-unattributed"},
                            "ForeignEmbeddedCurie": {
                                "role": "must-not-leak",
                                "project_key": str(distinct),
                            },
                        }
                    },
                    str(physical.resolve()): {
                        "agents": {
                            "CanonicalCurie": {
                                "role": "canonical",
                                "project_key": str(physical.resolve()),
                            }
                        }
                    },
                    str(distinct): {
                        "agents": {
                            "ForeignBucketCurie": {
                                "role": "must-not-leak",
                                "project_key": str(distinct),
                            }
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    substitutions.write_text(
        json.dumps(
            {
                "projects": {
                    str(alias): {
                        "LegacyAliasCurie": {
                            "requested": "Legacy-Alias-Curie",
                            "project_key": str(alias),
                        },
                        "UnkeyedCurie": {"requested": "Unkeyed-Curie"},
                        "ForeignEmbeddedCurie": {
                            "requested": "Foreign-Embedded-Curie",
                            "project_key": str(distinct),
                        },
                    },
                    str(physical.resolve()): {
                        "CanonicalCurie": {
                            "requested": "Canonical-Curie",
                            "project_key": str(physical.resolve()),
                        }
                    },
                    str(distinct): {
                        "ForeignBucketCurie": {
                            "requested": "Foreign-Bucket-Curie",
                            "project_key": str(distinct),
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "ANNOT_PATH", str(annotations))
    monkeypatch.setattr(
        dashboard_server, "LEGACY_ANNOT_PATH", str(tmp_path / "absent-legacy.json")
    )
    monkeypatch.setattr(dashboard_server, "SUBST_PATH", str(substitutions))
    dashboard_server._ANNOT_CACHE.update(path="", mtime=-1.0, data={})
    dashboard_server._SUBST_CACHE.update(path="", mtime=-1.0, data={})

    observed_annotations = dashboard_server._annotations(str(physical.resolve()))
    observed_substitutions = dashboard_server._name_substitutions(
        str(physical.resolve())
    )

    assert set(observed_annotations) == {"LegacyAliasCurie", "CanonicalCurie"}
    assert observed_annotations["LegacyAliasCurie"]["role"] == "legacy-alias"
    assert observed_annotations["CanonicalCurie"]["role"] == "canonical"
    assert observed_substitutions == {
        "LegacyAliasCurie": "Legacy-Alias-Curie",
        "CanonicalCurie": "Canonical-Curie",
    }


def test_dashboard_metadata_writes_preserve_unattributed_legacy_entries(
    tmp_path: pathlib.Path,
    project_paths: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "dashboard-preserved-metadata"
    runtime.mkdir()
    annotations = runtime / "annotations.json"
    substitutions = runtime / "name-substitutions.json"
    ambiguous_annotation = {
        "role": "historical-unattributed",
        "emoji": "?",
        "group": "legacy",
    }
    ambiguous_substitution = {
        "requested": "Ambiguous-Curie",
        "ts": "2026-09-12T00:00:00Z",
    }
    annotations.write_text(
        json.dumps({"AmbiguousCurie": ambiguous_annotation}), encoding="utf-8"
    )
    substitutions.write_text(
        json.dumps({"AmbiguousCurie": ambiguous_substitution}), encoding="utf-8"
    )
    monkeypatch.setattr(dashboard_server, "RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(dashboard_server, "ANNOT_PATH", str(annotations))
    monkeypatch.setattr(
        dashboard_server, "LEGACY_ANNOT_PATH", str(tmp_path / "absent-legacy.json")
    )
    monkeypatch.setattr(dashboard_server, "SUBST_PATH", str(substitutions))

    assert dashboard_server._write_annotation(
        "ScopedCurie", "scoped", "", "new", str(project_paths.main)
    )["ok"]
    dashboard_server._record_name_substitution(
        "ScopedCurie", "Scoped-Curie", str(project_paths.main)
    )

    annotation_store = json.loads(annotations.read_text(encoding="utf-8"))
    substitution_store = json.loads(substitutions.read_text(encoding="utf-8"))
    assert annotation_store["AmbiguousCurie"] == ambiguous_annotation
    assert substitution_store["AmbiguousCurie"] == ambiguous_substitution
    assert (
        annotation_store["projects"][str(project_paths.main)]["agents"]["ScopedCurie"]
        ["project_key"]
        == str(project_paths.main)
    )
    assert (
        substitution_store["projects"][str(project_paths.main)]["ScopedCurie"]
        ["project_key"]
        == str(project_paths.main)
    )


def test_dashboard_metadata_cache_never_crosses_project_or_store_path(
    tmp_path: pathlib.Path,
    project_paths: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_annotations = tmp_path / "annotations-a.json"
    first_substitutions = tmp_path / "substitutions-a.json"
    first_annotations.write_text(
        json.dumps(
            {
                "SharedCurie": {
                    "role": "project-a",
                    "project_key": str(project_paths.main),
                }
            }
        ),
        encoding="utf-8",
    )
    first_substitutions.write_text(
        json.dumps(
            {
                "SharedCurie": {
                    "requested": "Shared-Curie",
                    "project_key": str(project_paths.main),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "ANNOT_PATH", str(first_annotations))
    monkeypatch.setattr(
        dashboard_server, "LEGACY_ANNOT_PATH", str(tmp_path / "absent-legacy.json")
    )
    monkeypatch.setattr(dashboard_server, "SUBST_PATH", str(first_substitutions))
    dashboard_server._ANNOT_CACHE.update(path="", mtime=-1.0, project_key="", data={})
    dashboard_server._SUBST_CACHE.update(
        path="", mtime=-1.0, project_key="", data={}
    )
    assert dashboard_server._annotations(str(project_paths.main))
    assert dashboard_server._name_substitutions(str(project_paths.main))

    # A parse failure under another selected project cannot reuse A's cache.
    first_annotations.write_text("{broken", encoding="utf-8")
    first_substitutions.write_text("{broken", encoding="utf-8")
    assert dashboard_server._annotations(str(project_paths.other)) == {}
    assert dashboard_server._name_substitutions(str(project_paths.other)) == {}

    # Nor may a different store path with the same mtime reuse cached values.
    second_annotations = tmp_path / "annotations-b.json"
    second_substitutions = tmp_path / "substitutions-b.json"
    second_annotations.write_text("{broken", encoding="utf-8")
    second_substitutions.write_text("{broken", encoding="utf-8")
    cached_annotation_mtime = dashboard_server._ANNOT_CACHE["mtime"]
    cached_substitution_mtime = dashboard_server._SUBST_CACHE["mtime"]
    os.utime(second_annotations, (cached_annotation_mtime, cached_annotation_mtime))
    os.utime(
        second_substitutions,
        (cached_substitution_mtime, cached_substitution_mtime),
    )
    monkeypatch.setattr(dashboard_server, "ANNOT_PATH", str(second_annotations))
    monkeypatch.setattr(dashboard_server, "SUBST_PATH", str(second_substitutions))
    assert dashboard_server._annotations(str(project_paths.main)) == {}
    assert dashboard_server._name_substitutions(str(project_paths.main)) == {}


def test_dashboard_metadata_fails_closed_without_a_selected_project(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotations = tmp_path / "unconfigured-annotations.json"
    substitutions = tmp_path / "unconfigured-substitutions.json"
    monkeypatch.setattr(dashboard_server, "ANNOT_PATH", str(annotations))
    monkeypatch.setattr(
        dashboard_server, "LEGACY_ANNOT_PATH", str(tmp_path / "absent-legacy.json")
    )
    monkeypatch.setattr(dashboard_server, "SUBST_PATH", str(substitutions))
    monkeypatch.setattr(dashboard_server, "_project_key", lambda: "")

    annotation_shapes = [
        {"HistoricalCurie": {"role": "must-not-leak"}},
        {
            "projects": {
                "": {
                    "agents": {
                        "EmptyBucketCurie": {
                            "role": "must-not-leak",
                            "project_key": "",
                        }
                    }
                }
            }
        },
    ]
    substitution_shapes = [
        {"HistoricalCurie": {"requested": "ForeignCurie"}},
        {
            "projects": {
                "": {
                    "EmptyBucketCurie": {
                        "requested": "ForeignCurie",
                        "project_key": "",
                    }
                }
            }
        },
    ]
    for annotation_shape, substitution_shape in zip(
        annotation_shapes, substitution_shapes, strict=True
    ):
        annotations.write_text(json.dumps(annotation_shape), encoding="utf-8")
        substitutions.write_text(json.dumps(substitution_shape), encoding="utf-8")
        dashboard_server._ANNOT_CACHE.update(
            path=str(annotations),
            mtime=annotations.stat().st_mtime,
            project_key="",
            data={"CachedCurie": {"role": "must-not-leak"}},
        )
        dashboard_server._SUBST_CACHE.update(
            path=str(substitutions),
            mtime=substitutions.stat().st_mtime,
            project_key="",
            data={"CachedCurie": "ForeignCurie"},
        )

        assert dashboard_server._annotations() == {}
        assert dashboard_server._name_substitutions() == {}


def test_shell_substitution_writer_preserves_legacy_and_scopes_verified_entries(
    tmp_path: pathlib.Path,
    project_paths: SimpleNamespace,
) -> None:
    runtime = tmp_path / "shell-substitution-runtime"
    runtime.mkdir()
    store = runtime / "name-substitutions.json"
    ambiguous = {
        "requested": "Ambiguous-Curie",
        "ts": "2026-09-12T00:00:00Z",
    }
    verified = {
        "requested": "Verified-Curie",
        "project_key": str(project_paths.other),
        "ts": "2026-09-12T00:00:00Z",
    }
    store.write_text(
        json.dumps({"AmbiguousCurie": ambiguous, "VerifiedCurie": verified}),
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("AGENTSTACK_")}
    env.update(
        {
            "AGENTSTACK_RUNTIME_DIR": str(runtime),
            "AGENTSTACK_PYTHON": sys.executable,
        }
    )
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'source "{REGISTER_LIB}"\n'
            f'ags_record_name_substitution ScopedCurie Scoped-Curie "{project_paths.main}"',
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["AmbiguousCurie"] == ambiguous
    assert (
        data["projects"][str(project_paths.other)]["VerifiedCurie"] == verified
    )
    assert (
        data["projects"][str(project_paths.main)]["ScopedCurie"]["project_key"]
        == str(project_paths.main)
    )


def test_same_name_writes_and_delete_preserve_other_project(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(dashboard_server, "RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(dashboard_server, "ANNOT_PATH", str(runtime / "annotations.json"))
    monkeypatch.setattr(dashboard_server, "LEGACY_ANNOT_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(dashboard_server, "SUBST_PATH", str(runtime / "name-substitutions.json"))
    for project, role, requested in (("project-a", "auditor", "A-Curie"),
                                      ("project-b", "builder", "B-Curie")):
        assert dashboard_server._write_annotation("SharedCurie", role, "", "", project)["ok"]
        dashboard_server._record_name_substitution("SharedCurie", requested, project)
    assert dashboard_server._annotations("project-a")["SharedCurie"]["role"] == "auditor"
    assert dashboard_server._annotations("project-b")["SharedCurie"]["role"] == "builder"
    assert dashboard_server._name_substitutions("project-a") == {"SharedCurie": "A-Curie"}
    assert dashboard_server._name_substitutions("project-b") == {"SharedCurie": "B-Curie"}
    assert dashboard_server._write_annotation("SharedCurie", "", "", "", "project-a")["ok"]
    assert dashboard_server._annotations("project-a") == {}
    assert dashboard_server._annotations("project-b")["SharedCurie"]["role"] == "builder"
    monkeypatch.setattr(dashboard_server, "_project_key", lambda: "project-a")
    before = (runtime / "annotations.json").read_bytes()
    assert not dashboard_server._write_annotation("SharedCurie", "bad", "", "", "")["ok"]
    assert (runtime / "annotations.json").read_bytes() == before
    assert dashboard_server._annotations("") == {}
    assert dashboard_server._name_substitutions("") == {}
