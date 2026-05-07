"""Seed Book 5 calibration probe bank.

Domains are stable keys consumed by seed_router.py:
- infrastructure
- trading
- models_ai
- general_reasoning
- seed
"""

from __future__ import annotations

from seed_probes import Probe


PROBE_BANK = [
    # Infrastructure -------------------------------------------------------
    Probe(
        id="infra_docker_wsl2_bind_mount_reboot",
        domain="infrastructure",
        question=(
            "On Docker Desktop with WSL2, a Postgres container uses a bind mount or named volume. "
            "Will the data survive a Windows reboot, and what actually stops or persists?"
        ),
        correct_patterns=[r"surviv(e|es)|persist", r"container.*stop|restart", r"volume|bind mount"],
        fail_patterns=[r"data (is )?lost.*reboot", r"reboot.*delete(s)?\s+(the\s+)?volume"],
        score_type="binary",
    ),
    Probe(
        id="infra_pg_dump_custom_vs_plain",
        domain="infrastructure",
        question=(
            "For a PostgreSQL backup intended for reliable restore and object selection, should I prefer "
            "pg_dump -Fc or a plain SQL dump? Explain the operational difference."
        ),
        correct_patterns=[r"-Fc|custom format", r"pg_restore", r"selective|parallel|flexib"],
        fail_patterns=[r"plain SQL.*always.*safer", r"-Fc.*data loss"],
        score_type="binary",
    ),
    Probe(
        id="infra_nssm_vs_docker_dns",
        domain="infrastructure",
        question=(
            "A Windows NSSM service needs to connect to Postgres running in Docker Compose. "
            "Can it resolve the Compose service name the same way containers can?"
        ),
        correct_patterns=[r"Docker DNS|compose network|service name", r"inside.*container|same network", r"host port|localhost|published port"],
        fail_patterns=[r"NSSM.*resolve.*service name", r"Windows.*Docker DNS.*automatically"],
        score_type="binary",
    ),
    Probe(
        id="infra_compose_down_v_consequences",
        domain="infrastructure",
        question=(
            "What is the consequence of running docker compose down -v against a stack with a Postgres volume?"
        ),
        correct_patterns=[r"remove(s)?\s+.*volume", r"delete(s)?\s+.*data", r"data loss|destructive"],
        fail_patterns=[r"safe", r"preserve(s)?\s+.*volume", r"only stop(s)? containers"],
        score_type="binary",
    ),
    Probe(
        id="infra_git_allow_unrelated_histories",
        domain="infrastructure",
        question=(
            "When is git merge --allow-unrelated-histories appropriate, and why is it not a normal conflict flag?"
        ),
        correct_patterns=[r"unrelated histories|separate roots", r"two repositories|independent repos", r"not.*normal conflict"],
        fail_patterns=[r"always use", r"fix(es)? merge conflicts"],
        score_type="0-2",
    ),

    # Trading --------------------------------------------------------------
    Probe(
        id="trading_es_futures_roll_mechanics",
        domain="trading",
        question=(
            "Explain ES futures roll mechanics. What changes when rolling the front-month contract, "
            "and what should a backtest do with continuous data?"
        ),
        correct_patterns=[r"quarterly|Mar|Jun|Sep|Dec", r"volume|open interest", r"back[- ]?adjust|continuous"],
        fail_patterns=[r"never roll", r"same contract forever"],
        score_type="0-2",
    ),
    Probe(
        id="trading_fomc_session_timing",
        domain="trading",
        question=(
            "For US equity index futures, what is special about FOMC decision-day timing and pre/post behavior?"
        ),
        correct_patterns=[r"2\s*:?00\s*(p\.m\.|pm).*ET|14:00.*ET", r"press conference", r"volatility|whipsaw|liquidity"],
        fail_patterns=[r"market closes.*FOMC", r"no intraday effect"],
        score_type="0-2",
    ),
    Probe(
        id="trading_negative_sharpe_interpretation",
        domain="trading",
        question="What does a negative Sharpe ratio mean for a trading strategy?",
        correct_patterns=[r"below.*risk[- ]?free|underperform", r"negative excess return", r"per unit.*volatility|risk"],
        fail_patterns=[r"good", r"profitable.*because.*negative", r"lower risk.*therefore better"],
        score_type="binary",
    ),
    Probe(
        id="trading_lookahead_bias",
        domain="trading",
        question=(
            "Define look-ahead bias in a backtest and give one example of how it can sneak into a strategy."
        ),
        correct_patterns=[r"future information", r"not available.*time", r"close.*before.*decision|survivorship|revised data"],
        fail_patterns=[r"not a problem", r"only live trading"],
        score_type="binary",
    ),
    Probe(
        id="trading_rth_eth_dst_boundaries",
        domain="trading",
        question=(
            "How should a backtest define RTH vs ETH session boundaries for ES, especially around DST?"
        ),
        correct_patterns=[r"regular trading hours|RTH", r"electronic|extended trading hours|ETH", r"exchange time|timezone|DST"],
        fail_patterns=[r"fixed UTC.*all year", r"DST.*ignore"],
        score_type="0-2",
    ),

    # Models / AI ----------------------------------------------------------
    Probe(
        id="models_moe_vs_dense",
        domain="models_ai",
        question="What is the architecture difference between an MoE model and a dense model?",
        correct_patterns=[r"experts", r"router|gating", r"subset.*activated|not all parameters"],
        fail_patterns=[r"all parameters.*active.*MoE", r"MoE.*same.*dense"],
        score_type="binary",
    ),
    Probe(
        id="models_prismaquant_vs_uniform",
        domain="models_ai",
        question=(
            "What does PrismaQuant do differently from uniform quantization? Answer in terms of allocation/granularity."
        ),
        correct_patterns=[r"mixed[- ]?precision", r"per[- ]?Linear|per layer|module", r"sensitivity|budget|allocator|knapsack"],
        fail_patterns=[r"same bit.*every", r"uniform.*all layers", r"only rounds.*equally"],
        score_type="0-2",
    ),
    Probe(
        id="models_context_memory_conflict",
        domain="models_ai",
        question=(
            "If a model's parametric memory conflicts with retrieved context, which should the system trust and why?"
        ),
        correct_patterns=[r"retrieved context|non[- ]?parametric", r"source|fresh|ground", r"parametric.*stale|training"],
        fail_patterns=[r"always trust.*model", r"ignore.*retrieved"],
        score_type="0-2",
    ),

    # General reasoning ----------------------------------------------------
    Probe(
        id="reasoning_backup_recency_vs_authority",
        domain="general_reasoning",
        question=(
            "You have two backup records: an older signed database dump from the production host and a newer "
            "unverified copy from a developer laptop. Which is more trustworthy for restoration decisions?"
        ),
        correct_patterns=[r"authority|provenance|signed|verified", r"newer.*not.*automatically", r"verify|checksum|source"],
        fail_patterns=[r"newer.*always", r"developer laptop.*more trustworthy.*because.*newer"],
        score_type="0-2",
    ),
    Probe(
        id="reasoning_sunk_cost",
        domain="general_reasoning",
        question=(
            "A team has spent 6 months on an approach that now looks inferior. How should sunk cost affect the decision?"
        ),
        correct_patterns=[r"should not|irrelevant", r"future costs|future benefits|expected value", r"sunk cost"],
        fail_patterns=[r"continue.*because.*spent", r"must finish.*investment"],
        score_type="binary",
    ),
    Probe(
        id="reasoning_correlation_causation",
        domain="general_reasoning",
        question="Why does correlation in data not by itself prove causation?",
        correct_patterns=[r"confound|third variable", r"reverse causation", r"experiment|identification|causal"],
        fail_patterns=[r"correlation.*proves causation", r"always causal"],
        score_type="binary",
    ),

    # Seed-specific --------------------------------------------------------
    Probe(
        id="seed_mutability_contract",
        domain="seed",
        question=(
            "In Seed's mutability contract, what kinds of things should be treated as immutable, mutable, "
            "and append-only?"
        ),
        correct_patterns=[r"immutable", r"mutable", r"append[- ]?only", r"source|anchor|metadata|feedback|scar"],
        fail_patterns=[r"everything.*mutable", r"rewrite.*history", r"delete.*scars"],
        score_type="0-2",
    ),
    Probe(
        id="seed_anchors_scars_not_rules",
        domain="seed",
        question="In Seed, why are anchors scars rather than rules?",
        correct_patterns=[r"scar", r"evidence|history|memory", r"not.*rule|not.*policy|not.*binding", r"context"],
        fail_patterns=[r"must always obey", r"hard rule", r"policy.*anchor"],
        score_type="0-2",
    ),
]
