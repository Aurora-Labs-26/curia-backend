"""
tests/test_imports.py
The cheapest possible smoke test — every active module imports cleanly.

If a refactor breaks an import (rename, deleted dep, syntax error), this catches
it without needing the DB, API keys, or any external services.
"""


def test_core_db_imports():
    from core.db.connection import (  # noqa: F401
        get_pool,
        close_pool,
        get_db,
        db_query,
        db_execute,
        db_fetchrow,
        db_create,
        db_select,
        db_update,
        db_delete,
        db_upsert,
    )


def test_core_kb_imports():
    from core.kb import (  # noqa: F401
        UserKB,
        Identity,
        Interests,
        Preferences,
        ListeningContext,
        Dislikes,
        load_kb,
        save_kb,
        empty_kb,
    )


def test_core_llm_config_imports():
    from core.llm_config import resolve, get_config, reload_config  # noqa: F401
    from core.llm_config.adapters import build_llm, build_embedder, build_tts  # noqa: F401


def test_core_prompts_imports():
    from core.prompts import (  # noqa: F401
        TRANSFORMATION_NAMES,
        TIER_1,
        TIER_2,
        Transformations,
        transformations,
        EvaluateIdeasBatch,
        EvaluateSingleIdea,
        evaluate_ideas_batch,
        evaluate_single_idea,
        GenerateOutline,
        generate_outline,
        GenerateTranscript,
        generate_transcript,
    )


def test_core_queue_imports():
    from core.queue import enqueue, dequeue, ack, fail, default_worker_id, Job  # noqa: F401


def test_core_embeddings_and_tts_imports():
    from core.embeddings import get_embedding  # noqa: F401
    from core.tts import synthesize_for_speaker, synthesize_line  # noqa: F401


def test_intelligence_imports():
    from intelligence.idea_generator import build_graph, run_idea_generator  # noqa: F401
    from intelligence.selector import select_episode_sources, get_source_insights  # noqa: F401


def test_studio_imports():
    from studio.briefing_builder import build_briefing_packet  # noqa: F401
    from studio.formats import get_format, FORMATS  # noqa: F401
    from studio.generator import (  # noqa: F401
        process_episode,
        generate_standing_show,
        generate_outline,
        generate_transcript,
    )
    from studio.shows.profiles import SHOW_PROFILES, SPEAKER_PROFILES  # noqa: F401


def test_optimization_imports():
    from optimization.guidelines import get_guidelines, list_tasks  # noqa: F401
    from optimization.rubrics import generate_judge_prompt, judge, Judgment  # noqa: F401
    from optimization.examples import (  # noqa: F401
        list_examples,
        create_example,
        delete_example,
        import_from_episode,
    )
    from optimization.runs import create_run, get_run, list_runs  # noqa: F401
    from optimization.runner import execute_run  # noqa: F401


def test_api_imports():
    from api.main import app  # noqa: F401
    from api.auth import current_user, current_user_id, qa_required, CurrentUser  # noqa: F401
    from api.schemas import (  # noqa: F401
        MeResponse,
        SourceSummary,
        EpisodeSummary,
        EpisodeDetail,
        RubricResponse,
    )


def test_worker_imports():
    from worker.main import main, _process_one  # noqa: F401
    from worker.handlers import HANDLERS  # noqa: F401

    assert set(HANDLERS) == {"ingest", "generate_ideas", "generate_episode", "optimize"}
