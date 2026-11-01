import { useRoleContext } from '../../context/RoleContext'

export function RoleSwitcher() {
  const { user } = useRoleContext()
  return <span aria-label="Authenticated role">{user?.role ?? 'Signed out'}</span>
}
