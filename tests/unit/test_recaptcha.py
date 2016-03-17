import socket
import urllib.parse
from os import environ

import pytest
import pretend
import requests
import responses

from warehouse import recaptcha


_SETTINGS = {
    "recaptcha": {
        "site_key": "site_key_value",
        "secret_key": "secret_key_value",
    },
}
_REQUEST = pretend.stub(
    # returning a real requests.Session object because responses is responsible
    # for mocking that out
    http=requests.Session(),
    registry=pretend.stub(
        settings=_SETTINGS,
    ),
)


class TestVerifyResponse:
    @responses.activate
    def test_verify_service_disabled(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            body="",
        )
        serv = recaptcha.Service(
            pretend.stub(registry=pretend.stub(settings={}))
        )
        assert serv.verify_response('') is None
        assert not responses.calls

    @responses.activate
    def test_remote_ip_payload(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            json={"success": True},
        )
        serv = recaptcha.Service(_REQUEST)
        serv.verify_response("meaningless", remote_ip="ip")

        payload = dict(urllib.parse.parse_qsl(responses.calls[0].request.body))
        assert payload["remoteip"] == "ip"

    @responses.activate
    def test_unexpected_data_error(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            body="something awful",
        )
        serv = recaptcha.Service(_REQUEST)

        with pytest.raises(recaptcha.UnexpectedError) as err:
            serv.verify_response("meaningless")

        expected = "Unexpected data in response body: something awful"
        assert str(err.value) == expected

    @responses.activate
    def test_missing_success_key_error(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            json={"foo": "bar"},
        )
        serv = recaptcha.Service(_REQUEST)

        with pytest.raises(recaptcha.UnexpectedError) as err:
            serv.verify_response("meaningless")

        expected = "Missing 'success' key in response: {'foo': 'bar'}"
        assert str(err.value) == expected

    @responses.activate
    def test_missing_error_codes_key_error(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            json={"success": False},
        )
        serv = recaptcha.Service(_REQUEST)

        with pytest.raises(recaptcha.UnexpectedError) as err:
            serv.verify_response("meaningless")

        expected = "Response missing 'error-codes' key: {'success': False}"
        assert str(err.value) == expected

    @responses.activate
    def test_error_map_error(self):
        for key, exc_tp in recaptcha.ERROR_CODE_MAP.items():
            responses.add(
                responses.POST,
                recaptcha.VERIFY_URL,
                json={
                    "success": False,
                    "challenge_ts": 0,
                    "hostname": "hotname_value",
                    "error_codes": [key]
                }
            )

            serv = recaptcha.Service(_REQUEST)
            with pytest.raises(exc_tp):
                serv.verify_response("meaningless")

            responses.reset()

    @responses.activate
    def test_error_map_unknown_error(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            json={
                "success": False,
                "challenge_ts": 0,
                "hostname": "hostname_value",
                "error_codes": ["slartibartfast"],
            },
        )

        serv = recaptcha.Service(_REQUEST)
        with pytest.raises(recaptcha.UnexpectedError) as err:
            serv.verify_response("meaningless")
            assert str(err) == "Unexpected error code: slartibartfast"

    @responses.activate
    def test_challenge_response_missing_timestamp_success(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            json={
                "success": True,
                "hostname": "hostname_value",
            },
        )

        serv = recaptcha.Service(_REQUEST)
        res = serv.verify_response("meaningless")

        assert isinstance(res, recaptcha.ChallengeResponse)
        assert res.challenge_ts is None
        assert res.hostname == "hostname_value"

    @responses.activate
    def test_challenge_response_missing_hostname_success(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            json={
                "success": True,
                "challenge_ts": 0,
            },
        )

        serv = recaptcha.Service(_REQUEST)
        res = serv.verify_response("meaningless")

        assert isinstance(res, recaptcha.ChallengeResponse)
        assert res.hostname is None
        assert res.challenge_ts == 0

    @responses.activate
    def test_challenge_response_success(self):
        responses.add(
            responses.POST,
            recaptcha.VERIFY_URL,
            json={
                "success": True,
                "hostname": "hostname_value",
                "challenge_ts": 0,
            },
        )

        serv = recaptcha.Service(_REQUEST)
        res = serv.verify_response("meaningless")

        assert isinstance(res, recaptcha.ChallengeResponse)
        assert res.hostname == "hostname_value"
        assert res.challenge_ts == 0

    @responses.activate
    def test_unexpected_error(self):
        serv = recaptcha.Service(_REQUEST)
        serv.request.http.post = pretend.raiser(socket.error)

        with pytest.raises(recaptcha.UnexpectedError):
            serv.verify_response("meaningless")


class TestCSPPolicy:
    def test_csp_policy(self):
        scheme = 'https'
        request = pretend.stub(
            scheme=scheme,
            registry=pretend.stub(settings={
                "recaptcha": {
                    "site_key": "foo",
                    "secret_key": "bar",
                },
            })
        )
        serv = recaptcha.Service(request)
        assert serv.csp_policy == {
            "script-src": [
                "%s://www.google.com/recaptcha/" % scheme,
                "%s://www.gstatic.com/recaptcha/" % scheme,
            ],
            "frame-src": ["https://www.google.com/recaptcha/"],
            "style-src": ["'unsafe-inline'"],
        }

    def test_policy_when_disabled(self):
        settings = {}
        request = pretend.stub(
            registry=pretend.stub(settings=settings)
        )
        serv = recaptcha.Service(request)
        assert serv.csp_policy == {}


def test_service_factory():
    serv = recaptcha.service_factory(None, _REQUEST)
    assert serv.request is _REQUEST


def test_includeme():
    config = pretend.stub(
        register_service_factory=pretend.call_recorder(
            lambda fact, name: None
        ),
        add_settings=pretend.call_recorder(lambda settings: None),
    )
    recaptcha.includeme(config)

    assert config.register_service_factory.calls == [
        pretend.call(recaptcha.service_factory, name="recaptcha"),
    ]

    assert config.add_settings.calls == [
        pretend.call({
            "recaptcha": {
                "site_key": environ.get("RECAPTCHA_SITE_KEY"),
                "secret_key": environ.get("RECAPTCHA_SECRET_KEY"),
            },
        }),
    ]
