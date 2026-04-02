import asyncio
import types

# Возвращаем asyncio.coroutine для старых библиотек
if not hasattr(asyncio, "coroutine"):
    asyncio.coroutine = types.coroutine
