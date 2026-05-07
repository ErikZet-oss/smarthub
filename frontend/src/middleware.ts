import { jwtVerify } from "jose";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { SMARTHUB_SESSION_COOKIE } from "@/lib/auth-config";

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const secretRaw = process.env.SMARTHUB_AUTH_SECRET;

  if (!secretRaw || secretRaw.length < 16) {
    if (pathname.startsWith("/login")) {
      return NextResponse.next();
    }
    if (pathname.startsWith("/api/auth/login")) {
      return NextResponse.next();
    }
    return NextResponse.redirect(
      new URL("/login?error=config", request.url),
    );
  }

  const secret = new TextEncoder().encode(secretRaw);

  const verifySession = async (token: string) => {
    const { payload } = await jwtVerify(token, secret);
    if (payload.uid == null) {
      throw new Error("missing uid");
    }
  };

  if (pathname.startsWith("/login")) {
    const token = request.cookies.get(SMARTHUB_SESSION_COOKIE)?.value;
    if (token) {
      try {
        await verifySession(token);
        return NextResponse.redirect(new URL("/", request.url));
      } catch {
        // neplatná relácia — zobraz prihlásenie
      }
    }
    return NextResponse.next();
  }

  if (pathname.startsWith("/api/auth/")) {
    return NextResponse.next();
  }

  if (
    pathname.startsWith("/_next") ||
    pathname === "/favicon.ico" ||
    /\.(ico|png|jpg|jpeg|svg|gif|webp)$/i.test(pathname)
  ) {
    return NextResponse.next();
  }

  const token = request.cookies.get(SMARTHUB_SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  try {
    await verifySession(token);
    return NextResponse.next();
  } catch {
    const res = NextResponse.redirect(
      new URL("/login?error=session", request.url),
    );
    res.cookies.set(SMARTHUB_SESSION_COOKIE, "", {
      path: "/",
      maxAge: 0,
    });
    return res;
  }
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
