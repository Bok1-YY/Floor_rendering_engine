"""Operation-level billing metadata helpers shared by edit/polish workflows."""


def failure_metadata(error) -> dict:
    return {
        'operation_failure_code': str(getattr(error, 'failure_code', 'provider_failure')),
        'operation_retry_safety': str(getattr(error, 'retry_safety', 'fatal')),
        'operation_may_have_been_billed': bool(getattr(error, 'may_have_been_billed', False)),
        'operation_attempts': list(getattr(error, 'attempts', []) or []),
    }


__all__ = ['failure_metadata']
