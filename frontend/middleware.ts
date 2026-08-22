import { NextRequest, NextResponse } from "next/server";

function constantTimeEqual(left: string, right: string): boolean {
  const length = Math.max(left.length, right.length);
  let difference = left.length ^ right.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return difference === 0;
}

export function middleware(request: NextRequest) {
  const username = process.env.APP_BASIC_AUTH_USER;
  const password = process.env.APP_BASIC_AUTH_PASSWORD;
  const headers = new Headers({ "Cache-Control": "private, no-store" });

  if (!username || !password) {
    return NextResponse.next({ headers });
  }

  const authorization = request.headers.get("authorization") || "";
  const encoded = authorization.startsWith("Basic ") ? authorization.slice(6) : "";
  let suppliedUser = "";
  let suppliedPassword = "";
  try {
    const decoded = atob(encoded);
    const separator = decoded.indexOf(":");
    if (separator >= 0) {
      suppliedUser = decoded.slice(0, separator);
      suppliedPassword = decoded.slice(separator + 1);
    }
  } catch {
    /* malformed credentials receive the same challenge as missing credentials */
  }

  if (constantTimeEqual(suppliedUser, username) && constantTimeEqual(suppliedPassword, password)) {
    return NextResponse.next({ headers });
  }

  headers.set("WWW-Authenticate", 'Basic realm="Foldsmith preview"');
  return new NextResponse("Authentication required", { status: 401, headers });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
