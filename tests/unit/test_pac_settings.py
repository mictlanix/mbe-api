"""PAC settings (spec 039, research R4 and R5): the simulator is never allowed in production."""

import pytest

import app.main as main
from app.core.config import Settings, check_pac_settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.services import pac
from app.services.pac import get_pac_provider, require_provider
from app.services.pac.simulated import SimulatedPac


@pytest.fixture(autouse=True)
def _isolated_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings(_env_file=None)` reads the process environment, so pin what these tests rely on."""
    monkeypatch.setenv('BUSINESS_TIMEZONE', 'America/Mexico_City')
    monkeypatch.delenv('APP_ENV', raising=False)
    monkeypatch.delenv('PAC_PROVIDER', raising=False)


class TestDefaults:
    def test_a_missing_environment_is_production_without_a_provider(self) -> None:
        """`_env_file=None`, so a developer's own `.env` cannot change the result."""
        settings = Settings(_env_file=None)

        assert settings.app_env == 'production'
        assert settings.pac_provider == 'none'


class TestCheckPacSettings:
    def test_the_simulator_in_production_is_refused_naming_both_settings(self) -> None:
        settings = Settings(_env_file=None, app_env='production', pac_provider='simulated')

        with pytest.raises(RuntimeError) as error:
            check_pac_settings(settings)

        assert 'pac_provider' in str(error.value)
        assert 'app_env' in str(error.value)

    @pytest.mark.parametrize(
        ('app_env', 'pac_provider'),
        [
            ('production', 'none'),
            ('staging', 'none'),
            ('staging', 'simulated'),
            ('development', 'none'),
            ('development', 'simulated'),
        ],
    )
    def test_every_other_combination_passes(self, app_env: str, pac_provider: str) -> None:
        check_pac_settings(Settings(_env_file=None, app_env=app_env, pac_provider=pac_provider))


class TestGetPacProvider:
    def test_none_means_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pac.settings, 'pac_provider', 'none')

        assert get_pac_provider() is None

    def test_simulated_builds_the_simulator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pac.settings, 'pac_provider', 'simulated')

        assert isinstance(get_pac_provider(), SimulatedPac)

    def test_a_missing_provider_answers_503(self) -> None:
        with pytest.raises(DomainError) as error:
            require_provider(None)

        assert error.value.code == ErrorCode.PAC_NOT_CONFIGURED
        assert error.value.status_code == 503

    def test_a_provider_passes_through(self) -> None:
        provider = object()

        assert require_provider(provider) is provider  # type: ignore[arg-type]


class TestLifespanRefusesTheSimulatorInProduction:
    @pytest.mark.asyncio
    async def test_the_boot_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main.settings, 'app_env', 'production')
        monkeypatch.setattr(main.settings, 'pac_provider', 'simulated')

        with pytest.raises(RuntimeError, match='app_env'):
            async with main.lifespan(main.app):
                pytest.fail('the application must not start')
