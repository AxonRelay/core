import NextAuth from "next-auth"
import Google from "next-auth/providers/google"

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  ],
  callbacks: {
    async signIn({ user, account, profile }) {
      // Sync user to backend database
      if (user.email && account) {
        try {
          const apiUrl = process.env.NEXT_PUBLIC_API_URL || "/api"
          const response = await fetch(`${apiUrl}/auth/sync`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              email: user.email,
              name: user.name || null,
              oauth_provider: account.provider,
              oauth_id: account.providerAccountId,
            }),
          })

          if (!response.ok) {
            console.error("Failed to sync user to backend:", await response.text())
            // Allow sign-in even if backend sync fails (for resilience)
          }
        } catch (error) {
          console.error("Error syncing user to backend:", error)
          // Allow sign-in even if backend sync fails
        }
      }
      return true
    },
    async session({ session, token }) {
      // Add user ID to session
      if (token.sub) {
        session.user.id = token.sub
      }
      return session
    },
    async jwt({ token, user, account }) {
      // Persist user ID in JWT token
      if (user) {
        token.sub = user.id
      }
      return token
    },
  },
  pages: {
    signIn: "/auth/signin",
    error: "/auth/error",
  },
})
