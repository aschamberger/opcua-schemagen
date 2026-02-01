import ast

import pytest
from transitions.extensions import HierarchicalAsyncGraphMachine

import schemas.machinery_jobs as machinery_jobs


@pytest.fixture
def machine():
    """Provides a fresh state machine for each test."""

    statemachine_dict = ast.literal_eval(
        machinery_jobs.JobOrderControl.Config.opcua_state_machine
    )

    class MachineryJobs:
        pass

    mj = MachineryJobs()

    states = list(statemachine_dict["states"].keys()) + ["InitialState", "EndState"]
    machine = HierarchicalAsyncGraphMachine(
        mj,
        states=states,
        initial="InitialState",
        auto_transitions=False,
    )
    return machine


def test_state_machine_states(machine):
    states = machine.states.keys()
    assert "InitialState" in states
    assert "EndState" in states
    assert "Running" in states
    assert "Interrupted" in states
    assert "Aborted" in states
    assert "Ended" in states
    assert "AllowedToStart" in states
    assert "NotAllowedToStart" in states
