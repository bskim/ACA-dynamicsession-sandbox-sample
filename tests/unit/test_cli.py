from aca_agent_sample.cli import main


def test_offline_dry_runs_do_not_load_configuration(monkeypatch, capsys):
    def fail():
        raise AssertionError("Configuration must not be accessed")
    monkeypatch.setattr("aca_agent_sample.cli.load_config", fail)
    assert main(["dynamic-session", "--dry-run"]) == 0
    assert main(["sandbox"]) == 0
    assert "simulated" in capsys.readouterr().out


def test_foundry_live_is_opt_in(monkeypatch, capsys):
    from aca_agent_sample.config import SampleConfig
    config = SampleConfig(subscription_id="sub", tenant_id="tenant", foundry_openai_base_url="https://example.test/openai/v1/")
    monkeypatch.setattr("aca_agent_sample.cli.load_config", lambda: config)
    monkeypatch.setattr("aca_agent_sample.cli.create_credential", lambda config: (_ for _ in ()).throw(AssertionError("unexpected")))
    assert main(["preflight", "foundry-smoke"]) == 2
    assert "disabled" in capsys.readouterr().out


def test_live_failure_does_not_print_exception_details(monkeypatch, capsys):
    from aca_agent_sample.config import SampleConfig
    config = SampleConfig(subscription_id="sub", tenant_id="tenant", foundry_openai_base_url="https://example.test/openai/v1/", enable_live_azure=True)
    monkeypatch.setattr("aca_agent_sample.cli.load_config", lambda: config)
    monkeypatch.setattr("aca_agent_sample.cli.create_credential", lambda config: (_ for _ in ()).throw(ValueError("sensitive value")))
    assert main(["preflight", "foundry-smoke"]) == 1
    assert "sensitive value" not in capsys.readouterr().out
