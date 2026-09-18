import { useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { ApiError } from '@/lib/api'
import { useLogin } from '@/lib/auth'

export function AdminLogin() {
  const navigate = useNavigate()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    login.mutate(
      { email, password },
      {
        onSuccess: () => void navigate({ to: '/admin/freelance' }),
        onError: (failure) =>
          setError(failure instanceof ApiError ? failure.message : 'Accesso non riuscito.'),
      },
    )
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-2xl border bg-card p-6">
        <h1 className="inline-flex items-center gap-2.5 text-xl font-semibold">
          <BrandMark className="size-3.5" />
          rebase · admin
        </h1>
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={login.isPending}>
          {login.isPending ? 'Accesso in corso…' : 'Accedi'}
        </Button>
      </form>
    </div>
  )
}
