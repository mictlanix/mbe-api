"""PAC settings (spec 039, research R4 and R5): the simulator is never allowed in production."""

import pytest
from pydantic import SecretStr

import app.main as main
from app.core.config import Settings, check_pac_settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.services import pac
from app.services.pac import get_pac_provider, require_provider
from app.services.pac.adapter import CfdiPacProvider
from app.services.pac.simulated import SimulatedPac


@pytest.fixture(autouse=True)
def _isolated_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings(_env_file=None)` reads the process environment, so pin what these tests rely on."""
    monkeypatch.setenv('BUSINESS_TIMEZONE', 'America/Mexico_City')
    monkeypatch.delenv('APP_ENV', raising=False)
    monkeypatch.delenv('PAC_PROVIDER', raising=False)


_USER, _PASSWORD = 'sentinel-user-0451', 'sentinel-password-0451'


def _multifacturas(**overrides: object) -> Settings:
    """A Multifacturas configuration that passes, changed by `overrides`."""
    values: dict = {
        'app_env': 'staging',
        'pac_provider': 'multifacturas',
        'multifacturas_user': _USER,
        'multifacturas_password': SecretStr(_PASSWORD),
        'multifacturas_production': False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TestDefaults:
    def test_a_missing_environment_is_production_with_no_pac_named(self) -> None:
        """`_env_file=None`, so a developer's own `.env` cannot change the result. The PAC has no
        default (spec 040, FR-016): unset is None, which `check_pac_settings` refuses."""
        settings = Settings(_env_file=None)

        assert settings.app_env == 'production'
        assert settings.pac_provider is None


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

    def test_an_unnamed_pac_is_refused(self) -> None:
        """Spec 040, FR-016: every `.env` names its PAC; there is no default."""
        with pytest.raises(RuntimeError) as error:
            check_pac_settings(Settings(_env_file=None, app_env='development'))

        assert 'PAC_PROVIDER' in str(error.value)

    @pytest.mark.parametrize(
        ('app_env', 'production'),
        [('staging', False), ('development', False), ('production', True)],
    )
    def test_multifacturas_in_its_matching_mode_passes(
        self, app_env: str, production: bool
    ) -> None:
        check_pac_settings(_multifacturas(app_env=app_env, multifacturas_production=production))

    @pytest.mark.parametrize(
        ('overrides', 'named'),
        [
            ({'multifacturas_user': None}, ['MULTIFACTURAS_USER']),
            ({'multifacturas_password': None}, ['MULTIFACTURAS_PASSWORD']),
            (
                {'multifacturas_user': None, 'multifacturas_password': None},
                ['MULTIFACTURAS_USER', 'MULTIFACTURAS_PASSWORD'],
            ),
            (
                {'app_env': 'staging', 'multifacturas_production': True},
                ['MULTIFACTURAS_PRODUCTION', 'APP_ENV'],
            ),
            (
                {'app_env': 'development', 'multifacturas_production': True},
                ['MULTIFACTURAS_PRODUCTION', 'APP_ENV'],
            ),
            (
                {'app_env': 'production', 'multifacturas_production': False},
                ['MULTIFACTURAS_PRODUCTION', 'APP_ENV'],
            ),
            ({'multifacturas_url': 'http://ws.multifacturas.com'}, ['MULTIFACTURAS_URL']),
            ({'multifacturas_connect_timeout': 0}, ['MULTIFACTURAS_CONNECT_TIMEOUT']),
            ({'multifacturas_read_timeout': 0}, ['MULTIFACTURAS_READ_TIMEOUT']),
        ],
    )
    def test_an_unsafe_multifacturas_configuration_is_refused(
        self, overrides: dict, named: list[str]
    ) -> None:
        with pytest.raises(RuntimeError) as error:
            check_pac_settings(_multifacturas(**overrides))

        message = str(error.value)
        for name in named:
            assert name in message
        assert _USER not in message and _PASSWORD not in message


class TestGetPacProvider:
    def test_an_unset_pac_means_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pac.settings, 'pac_provider', None)

        assert get_pac_provider() is None

    def test_none_means_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pac.settings, 'pac_provider', 'none')

        assert get_pac_provider() is None

    def test_simulated_builds_the_simulator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pac.settings, 'pac_provider', 'simulated')

        assert isinstance(get_pac_provider(), SimulatedPac)

    def test_multifacturas_builds_the_adapter_over_the_library(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Built from settings only, without reaching the network (spec 040, T009)."""
        monkeypatch.setattr(pac.settings, 'pac_provider', 'multifacturas')
        monkeypatch.setattr(pac.settings, 'multifacturas_user', 'user')
        monkeypatch.setattr(pac.settings, 'multifacturas_password', SecretStr('sentinel-0451'))
        monkeypatch.setattr(pac.settings, 'multifacturas_production', False)

        provider = get_pac_provider()

        assert isinstance(provider, CfdiPacProvider)
        assert (provider.name, provider.resolves_by_resubmitting) == ('multifacturas', True)
        assert 'sentinel-0451' not in repr(provider)

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
