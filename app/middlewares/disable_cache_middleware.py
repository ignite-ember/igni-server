from starlette.middleware.base import BaseHTTPMiddleware

#: Public by nature and fetched by every service that checks a token, so
#: they say how long they may be kept rather than being forced uncached.
#: See app/api/base_v1/endpoints/identity/discovery.py.
CACHEABLE_PREFIX = '/.well-known/'


class DisableCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(CACHEABLE_PREFIX):
            return response
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
