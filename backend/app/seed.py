"""Seed demo identities and a demo project so the happy path is three clicks.

Demo biology uses public-domain sequences only:
  * Scaffold: immunoglobulin-binding domain B1 of streptococcal protein G ("GB1", PDB 1PGA),
    a 56-residue model system for stability engineering.
  * Target: the CH3 domain of human IgG1 heavy chain (UniProt P01857), the natural binding partner.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DrugProgram, Project, User
from app.security import hash_api_key
from app.services import ingest
from app.versioning import get_branch

logger = logging.getLogger(__name__)

GB1 = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"
IGG1_CH3 = (
    "GQPREPQVYTLPPSRDELTKNQVSLTCLVKGFYPSDIAVEWESNGQPENNYKTTPPVLDSDGSFFLYSKLTVDKSRWQQGN"
    "VFSCSVMHEALHNHYTQKSLSLSPGK"
)

DEMO_FASTA = f""">GB1-wildtype immunoglobulin-binding domain B1, protein G (PDB 1PGA)
{GB1}
"""

DEMO_GOAL = (
    "Improve the thermal stability and solubility of the GB1 domain while preserving IgG1 Fc "
    "binding, and produce a wet-lab shortlist that is cheap enough to test in one round."
)

DEMO_BRIEF = (
    "Round 1: raise the stability proxy and cut aggregation risk without losing predicted Fc "
    "binding. Prioritise designs that test distinct mechanisms (surface charge vs core packing)."
)


def seed_users(db: Session) -> list[User]:
    settings = get_settings()
    spec = [
        ("admin@dybpro.demo", "Demo Admin", "admin", settings.seed_admin_api_key),
        ("scientist@dybpro.demo", "Demo Scientist", "scientist", settings.seed_scientist_api_key),
        ("viewer@dybpro.demo", "Demo Viewer", "viewer", settings.seed_viewer_api_key),
    ]
    users: list[User] = []
    for email, name, role, api_key in spec:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                name=name,
                role=role,
                api_key_hash=hash_api_key(api_key),
                acu_quota=settings.default_acu_quota,
                cycle_quota=settings.default_cycle_quota,
            )
            db.add(user)
        else:
            user.api_key_hash = hash_api_key(api_key)
            user.role = role
        users.append(user)
    db.flush()
    return users


def seed_demo_project(db: Session, owner_id: str | None = None) -> Project:
    """Idempotent: returns the existing demo project if it is already seeded."""
    existing = db.scalar(select(Project).where(Project.is_demo.is_(True)))
    if existing is not None:
        return existing

    project = Project(
        name="GB1 stability + Fc binding (demo)",
        goal=DEMO_GOAL,
        target_name="Human IgG1 CH3 domain",
        target_sequence=IGG1_CH3,
        owner_id=owner_id,
        is_demo=True,
    )
    db.add(project)
    db.flush()
    get_branch(db, project.id, "main", create=True)
    ingest.ingest_file(db, project, "gb1-wildtype.fasta", DEMO_FASTA.encode())
    db.flush()
    logger.info("seeded demo project %s", project.id)
    return project


DEMO_PROGRAM_OBJECTIVE = (
    "Find an oral small-molecule ligand for the demo pocket with a predicted potency and ADMET "
    "profile good enough to justify a first round of wet-lab assays. The target sequence here is a "
    "public-domain demo surrogate: the binding number is a sequence-derived complementarity proxy, "
    "not docking, so it ranks molecules and nothing more."
)

# Public, well-characterised molecules used only as starting fragments so the demo portfolio is
# not empty. They are not proposed as ligands for this target.
DEMO_SEED_SMILES = (
    ("frag-benzamide", "NC(=O)c1ccccc1"),
    ("frag-aminopyridine", "Nc1ccncc1"),
    ("frag-indole", "c1ccc2[nH]ccc2c1"),
    ("frag-anilide", "CC(=O)Nc1ccccc1"),
    ("frag-phenylpiperazine", "C1CN(CCN1)c1ccccc1"),
)


def seed_demo_program(db: Session, project: Project) -> DrugProgram | None:
    """Idempotent demo drug program so the Pharmakon board has something to show."""
    existing = db.scalar(select(DrugProgram).where(DrugProgram.is_demo.is_(True)))
    if existing is not None:
        return existing

    program = DrugProgram(
        project_id=project.id,
        name="Demo small-molecule program",
        target_name=project.target_name or "demo target",
        target_sequence=project.target_sequence,
        indication="demonstration only",
        objective=DEMO_PROGRAM_OBJECTIVE,
        autonomy_level=2,
        is_demo=True,
    )
    db.add(program)
    db.flush()
    from app.services.program import commit_molecules

    commits, rejected = commit_molecules(
        db,
        program,
        [
            {
                "label": label,
                "smiles": smiles,
                "rationale": "seeded demo fragment; not a proposed ligand for this target",
            }
            for label, smiles in DEMO_SEED_SMILES
        ],
        stage_key=program.current_stage,
        round_id=None,
        provider="human",
    )
    if rejected:
        logger.warning("demo program seed dropped %d unparseable fragments", len(rejected))
    db.flush()
    logger.info("seeded demo program %s with %d molecules", program.id, len(commits))
    return program


def seed_all(db: Session) -> Project:
    users = seed_users(db)
    scientist = next((u for u in users if u.role == "scientist"), None)
    project = seed_demo_project(db, owner_id=scientist.id if scientist else None)
    seed_demo_program(db, project)
    return project
