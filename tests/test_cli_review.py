from enterprise_agent_improvement_lab.cli import main
from enterprise_agent_improvement_lab.storage import SQLiteStore


def test_review_commands_show_stored_runs_and_lifecycle_evidence(tmp_path, experiment, capsys):
    database = tmp_path / "lab.sqlite3"
    with SQLiteStore(database) as store:
        store.experiments.save(experiment)

    assert main(["review", "runs", "--database", str(database)]) == 0
    assert experiment.run_id in capsys.readouterr().out

    assert main(["review", "lifecycle", "--database", str(database)]) == 0
    assert '"shadow":[]' in capsys.readouterr().out
