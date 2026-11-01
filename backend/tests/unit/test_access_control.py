from app.services.access_control import visible_caller_ids


def test_head_has_no_restriction(db_session):
    assert visible_caller_ids("head", "u-head", db_session) is None


def test_manager_sees_only_direct_reports(db_session):
    ids = visible_caller_ids("manager", "u-mgr1", db_session)
    assert ids == {"u-caller1", "u-caller2"}


def test_other_manager_sees_only_their_own_reports(db_session):
    ids = visible_caller_ids("manager", "u-mgr2", db_session)
    assert ids == {"u-caller3"}


def test_caller_sees_only_self(db_session):
    ids = visible_caller_ids("caller", "u-caller1", db_session)
    assert ids == {"u-caller1"}
