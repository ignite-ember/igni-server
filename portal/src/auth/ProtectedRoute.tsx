import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './useAuth'

/** Requires authentication only — used for /cli-auth. */
export function AuthRoute() {
  const { isAuthenticated, isChecking } = useAuth()
  const location = useLocation()
  // Bouncing to /login before the answer arrives would send away every
  // visitor to a deployment that signs them in without a stored token.
  if (isChecking) return null
  if (!isAuthenticated) {
    const returnTo = `${location.pathname}${location.search}`
    return <Navigate to={`/login?returnTo=${encodeURIComponent(returnTo)}`} replace />
  }
  return <Outlet />
}

/** Requires authentication — used for all app pages. */
export function ProtectedRoute() {
  const { isAuthenticated, isChecking } = useAuth()
  if (isChecking) return null
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <Outlet />
}
