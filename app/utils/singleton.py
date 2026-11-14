"""Singleton metaclass for creating singleton instances."""


class SingletonMeta(type):
    """Metaclass that creates a Singleton base type when called."""

    _instances: dict[type, object] = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]

    def forget(cls) -> None:
        """Drop the instance, so the next call builds a new one. For tests that swap one in."""
        cls._instances.pop(cls, None)
