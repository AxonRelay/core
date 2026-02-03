import { auth } from "@/auth"
import { redirect } from "next/navigation"
import Link from "next/link"
import { notFound } from "next/navigation"

interface User {
  id: number
  email: string
  name: string | null
}

interface ProjectMember {
  id: number
  user_id: number
  project_id: number
  role: string
  joined_at: string
  user: User
}

interface Project {
  id: number
  name: string
  description: string | null
  created_at: string
  updated_at: string
  members: ProjectMember[]
}

async function getProject(projectId: string, userId: number): Promise<Project | null> {
  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://backend:8000"
    const response = await fetch(`${apiUrl}/projects/${projectId}?user_id=${userId}`, {
      cache: "no-store",
    })

    if (response.status === 404 || response.status === 403) {
      return null
    }

    if (!response.ok) {
      console.error("Failed to fetch project:", await response.text())
      return null
    }

    return await response.json()
  } catch (error) {
    console.error("Error fetching project:", error)
    return null
  }
}

function getRoleBadgeColor(role: string): string {
  switch (role.toLowerCase()) {
    case "owner":
      return "bg-purple-100 text-purple-800"
    case "admin":
      return "bg-red-100 text-red-800"
    case "member":
      return "bg-blue-100 text-blue-800"
    case "reviewer":
      return "bg-green-100 text-green-800"
    case "viewer":
      return "bg-gray-100 text-gray-800"
    default:
      return "bg-gray-100 text-gray-800"
  }
}

export default async function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const session = await auth()

  if (!session?.user) {
    redirect("/auth/signin")
  }

  const { id } = await params

  // TODO: Get actual user ID from backend after syncing
  const userId = 1
  const project = await getProject(id, userId)

  if (!project) {
    notFound()
  }

  const currentUserMember = project.members.find((m) => m.user_id === userId)
  const isOwnerOrAdmin =
    currentUserMember?.role === "owner" || currentUserMember?.role === "admin"

  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Header */}
        <div className="mb-8">
          <Link
            href="/projects"
            className="text-sm text-blue-600 hover:text-blue-700 flex items-center"
          >
            <svg
              className="w-4 h-4 mr-1"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 19l-7-7 7-7"
              />
            </svg>
            Back to Projects
          </Link>
          <div className="mt-4 flex justify-between items-start">
            <div>
              <h1 className="text-3xl font-bold text-gray-900">{project.name}</h1>
              {project.description && (
                <p className="mt-2 text-gray-600">{project.description}</p>
              )}
              <p className="mt-1 text-sm text-gray-500">
                Created {new Date(project.created_at).toLocaleDateString()}
              </p>
            </div>
            {isOwnerOrAdmin && (
              <div className="flex space-x-2">
                <button className="px-4 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50">
                  Edit Project
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Members Section */}
        <div className="bg-white shadow rounded-lg">
          <div className="p-6 border-b border-gray-200">
            <div className="flex justify-between items-center">
              <h2 className="text-lg font-medium text-gray-900">Members</h2>
              {isOwnerOrAdmin && (
                <button className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700">
                  Add Member
                </button>
              )}
            </div>
          </div>

          <div className="divide-y divide-gray-200">
            {project.members.map((member) => (
              <div key={member.id} className="p-6 flex items-center justify-between">
                <div className="flex items-center">
                  <div className="flex-shrink-0">
                    <div className="h-10 w-10 rounded-full bg-gray-200 flex items-center justify-center">
                      <span className="text-gray-600 font-medium text-sm">
                        {member.user.name?.[0]?.toUpperCase() ||
                          member.user.email[0].toUpperCase()}
                      </span>
                    </div>
                  </div>
                  <div className="ml-4">
                    <p className="text-sm font-medium text-gray-900">
                      {member.user.name || member.user.email}
                    </p>
                    <p className="text-sm text-gray-500">{member.user.email}</p>
                  </div>
                </div>

                <div className="flex items-center space-x-4">
                  <span
                    className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getRoleBadgeColor(
                      member.role
                    )}`}
                  >
                    {member.role.toUpperCase()}
                  </span>
                  {isOwnerOrAdmin && member.user_id !== userId && (
                    <button className="text-sm text-gray-400 hover:text-gray-600">
                      <svg
                        className="h-5 w-5"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z"
                        />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Tasks Section (Placeholder) */}
        <div className="mt-8 bg-white shadow rounded-lg">
          <div className="p-6 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">Tasks</h2>
          </div>
          <div className="p-6 text-center text-gray-500">
            <p>Task management coming soon...</p>
          </div>
        </div>
      </div>
    </div>
  )
}
