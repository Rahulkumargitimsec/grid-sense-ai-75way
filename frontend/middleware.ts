import { NextRequest, NextResponse } from 'next/server';

const REFRESH_COOKIE_NAME = 'gridsense_refresh_token';

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (pathname === '/' || pathname === '/login') {
    return NextResponse.next();
  }

  if (!request.cookies.has(REFRESH_COOKIE_NAME)) {
    const loginUrl = new URL('/login', request.url);
    const returnPath = `${pathname}${search}`;
    loginUrl.searchParams.set('returnTo', returnPath.startsWith('/') && !returnPath.startsWith('//') ? returnPath : '/dashboard');
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|icon.svg|favicon.ico|gridsense-ai-development-plan.pdf).*)']
};
