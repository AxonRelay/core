import { auth, signIn, signOut } from "@/auth"
import Link from "next/link"

export default async function Header() {
  const session = await auth()

  return (
    <header className="bg-white border-b border-gray-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-16">
          <div className="flex items-center">
            <Link href="/" className="text-xl font-bold text-gray-900">
              AxonRelay
            </Link>
          </div>

          <nav className="flex items-center gap-6">
            {session?.user && (
              <Link
                href="/projects"
                className="text-sm font-medium text-gray-700 hover:text-gray-900"
              >
                Projects
              </Link>
            )}
            {session?.user ? (
              <>
                <div className="flex items-center gap-3">
                  {session.user.image && (
                    <img
                      src={session.user.image}
                      alt={session.user.name || "User"}
                      className="w-8 h-8 rounded-full"
                    />
                  )}
                  <span className="text-sm text-gray-700">
                    {session.user.name || session.user.email}
                  </span>
                </div>
                <form
                  action={async () => {
                    "use server"
                    await signOut({ redirectTo: "/" })
                  }}
                >
                  <button
                    type="submit"
                    className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
                  >
                    Sign out
                  </button>
                </form>
              </>
            ) : (
              <form
                action={async () => {
                  "use server"
                  await signIn("google", { redirectTo: "/" })
                }}
              >
                <button
                  type="submit"
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
                >
                  Sign in
                </button>
              </form>
            )}
          </nav>
        </div>
      </div>
    </header>
  )
}
