"""Finite State Machine states for user conversations."""

from aiogram.fsm.state import State, StatesGroup


class BeautyBookingStates(StatesGroup):
    """Client booking flow states."""

    waiting_for_service = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_date = State()
    waiting_for_time = State()


class ClientRegistrationStates(StatesGroup):
    """Standalone client self-registration (no master/service context yet)."""

    waiting_for_name = State()
    waiting_for_phone = State()


class MasterOnboardingStates(StatesGroup):
    """Master registration and profile management states."""

    waiting_for_master_name = State()
    waiting_for_service_input = State()
    waiting_for_service_price = State()
    waiting_for_duration = State()
    waiting_for_schedule = State()
    waiting_for_card = State()
    confirmation = State()
