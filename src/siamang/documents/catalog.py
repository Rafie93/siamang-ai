"""The outputs each agent owes. Code-owned; every entry always ends up with a recorded status.

``source`` says where a document's truth comes from, and is the guard against invented evidence:

* ``authored``: written by the agent's model; an opinion or a design, never a measurement;
* ``derived``: computed from deterministic pipeline results, never touched by a model;
* ``measured``: only real execution may fill it. No model is ever asked to write one.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class Source(StrEnum):
    AUTHORED = 'authored'
    DERIVED = 'derived'
    MEASURED = 'measured'


class DocumentStatus(StrEnum):
    GENERATED = 'generated'
    NOT_APPLICABLE = 'not_applicable'  # judged irrelevant, with a reason
    NOT_RUN = 'not_run'  # measured output with no real execution behind it
    NOT_ASSIGNED = 'not_assigned'  # the agent has no model
    FAILED = 'failed'


@dataclass(frozen=True)
class Spec:
    agent: str
    stage: str
    kind: str
    title: str
    source: Source = Source.AUTHORED
    blocks: tuple[str, ...] = ('mermaid',)  # fenced languages the owner may edit, see documents.diagrams


def _specs(agent, stage, source, *entries):
    return tuple(Spec(agent, stage, kind, title, source) for kind, title in entries)


CATALOG: tuple[Spec, ...] = (
    *_specs('lead', 'briefing', Source.AUTHORED,
            ('project_brief', 'Project Brief'), ('task_specification', 'Task Specification'),
            ('agent_assignment', 'Pembagian Tugas Antar-Agent')),
    *_specs('planner', 'planning', Source.AUTHORED,
            ('prd', 'Product Requirements Document (PRD)'), ('roadmap', 'Project Roadmap'),
            ('wbs', 'Work Breakdown Structure (WBS)'), ('task_list', 'Task List'),
            ('priorities', 'Prioritas Pekerjaan'), ('complexity', 'Estimasi Kompleksitas'),
            ('dependencies', 'Dependency Antar-Task'), ('acceptance_criteria', 'Acceptance Criteria')),
    *_specs('analyst', 'analysis', Source.AUTHORED,
            ('srs', 'Software Requirements Specification (SRS)'), ('architecture', 'System Architecture'),
            ('use_case_diagram', 'Use Case Diagram'), ('class_diagram', 'Class Diagram'),
            ('erd', 'ERD / Database Schema'), ('sequence_diagram', 'Sequence Diagram'),
            ('api_spec', 'API Specification'), ('technical_design', 'Technical Design Document')),
    *_specs('analyst', 'analysis', Source.DERIVED, ('work_packages', 'Paket Kerja Programmer')),
    # One model call writes the tokens, the wireframes that use them, and the screens they draw: parts of a stage
    # run side by side and cannot read each other.
    *_specs('designer', 'designing', Source.AUTHORED,
            ('design_tokens', 'Design System / Tokens'), ('wireframes', 'Wireframe per Layar'),
            ('ui_spec', 'Spesifikasi Layar dan Komponen'), ('user_flow', 'User Flow / Journey'),
            ('sitemap', 'Sitemap dan Navigasi'), ('accessibility', 'Aksesibilitas')),
    *_specs('programmer', 'programming', Source.DERIVED,
            ('source_code', 'Source Code'), ('file_structure', 'File dan Folder Project'),
            ('migration', 'Database Migration'), ('unit_tests', 'Unit Test Awal'), ('patch', 'Code Patch / Diff')),
    *_specs('programmer', 'programming', Source.AUTHORED,
            ('api_endpoints', 'API Endpoint'), ('implementation_notes', 'Dokumentasi Implementasi')),
    *_specs('qa', 'qa', Source.AUTHORED, ('test_plan', 'Test Plan'), ('test_cases', 'Test Cases')),
    *_specs('qa', 'qa', Source.MEASURED,
            ('unit_test_results', 'Unit Test Results'), ('integration_test_results', 'Integration Test Results'),
            ('functional_test_results', 'Functional Test Results'), ('coverage_report', 'Test Coverage Report'),
            ('ci_results', 'CI Build dan Test'), ('build_artifacts', 'Artefak Build')),
    *_specs('qa', 'qa', Source.DERIVED, ('bug_report', 'Bug Report'), ('qa_verdict', 'QA Verdict')),
    *_specs('reviewer', 'reviewing', Source.AUTHORED,
            ('code_review', 'Code Review Report'), ('architecture_compliance', 'Architecture Compliance Report'),
            ('security_findings', 'Security Findings'), ('performance_findings', 'Performance Findings'),
            ('maintainability', 'Maintainability Assessment')),
    *_specs('reviewer', 'reviewing', Source.DERIVED,
            ('findings_summary', 'Critical / Major / Minor Findings'), ('review_verdict', 'Review Verdict')),
    *_specs('lead', 'reporting', Source.AUTHORED,
            ('status_report', 'Status dan Progres Proyek'), ('final_report', 'Final Project Report'),
            ('recommendations', 'Rekomendasi Tindak Lanjut')),
)
EDITABLE_BLOCKS = {'wireframes': ('mermaid', 'html'), 'design_tokens': ('mermaid', 'json')}
CATALOG = tuple(replace(s, blocks=EDITABLE_BLOCKS[s.kind]) if s.kind in EDITABLE_BLOCKS else s for s in CATALOG)
AGENTS = ('lead', 'planner', 'analyst', 'designer', 'programmer', 'qa', 'reviewer')
SPECS = {spec.kind: spec for spec in CATALOG}


def specs_for(stage: str, source: Source | None = None) -> tuple[Spec, ...]:
    return tuple(s for s in CATALOG if s.stage == stage and (source is None or s.source is source))
