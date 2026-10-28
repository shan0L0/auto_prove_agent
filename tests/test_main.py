from agent.main import _make_config, _review_retries, build_parser


def test_default_help_hides_advanced_options():
    help_text = build_parser().format_help()

    assert "--profile" in help_text
    assert "--beam-width" not in help_text
    assert "--review-retries" not in help_text


def test_advanced_help_shows_tuning_options():
    help_text = build_parser(show_advanced=True).format_help()

    assert "--beam-width" in help_text
    assert "--workers" in help_text
    assert "--review-retries" in help_text


def test_profile_supplies_config_defaults_and_can_be_overridden():
    parser = build_parser()
    args = parser.parse_args(["--task-dir", "dataset/0001", "--profile", "quick", "--max-depth", "2"])

    config = _make_config(args)

    assert config.beam_width == 2
    assert config.max_rounds == 1
    assert config.max_depth == 2
    assert _review_retries(args) == 1


def test_workers_option_sets_parallel_sorry_workers():
    parser = build_parser()
    args = parser.parse_args(["--task-dir", "dataset/0001", "--workers", "3", "--tactic-workers", "2"])

    config = _make_config(args)

    assert config.max_workers == 3
    assert config.tactic_workers == 2


def test_review_profile_enables_one_reviewer_retry():
    args = build_parser().parse_args(["--task-dir", "dataset/0001", "--profile", "review"])

    assert _review_retries(args) == 1


def test_default_config_filters_low_score_candidates():
    args = build_parser().parse_args(["--task-dir", "dataset/0001"])

    config = _make_config(args)

    assert config.min_candidate_score > 0.0


def test_ignore_candidate_score_flag_disables_filter():
    args = build_parser(show_advanced=True).parse_args(
        ["--task-dir", "dataset/0001", "--ignore-candidate-score"]
    )

    config = _make_config(args)

    assert config.min_candidate_score == 0.0


def test_triage_is_enabled_by_default():
    args = build_parser().parse_args(["--task-dir", "dataset/0001"])

    config = _make_config(args)

    assert config.enable_triage is True


def test_no_triage_flag_disables_triage():
    args = build_parser(show_advanced=True).parse_args(
        ["--task-dir", "dataset/0001", "--no-triage"]
    )

    config = _make_config(args)

    assert config.enable_triage is False


def test_triage_abandon_requires_explicit_flag():
    default_args = build_parser().parse_args(["--task-dir", "dataset/0001"])
    abandon_args = build_parser(show_advanced=True).parse_args(
        ["--task-dir", "dataset/0001", "--triage-abandon"]
    )

    assert _make_config(default_args).triage_abandons is False
    assert _make_config(abandon_args).triage_abandons is True
