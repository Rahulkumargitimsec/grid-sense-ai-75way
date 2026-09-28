import type { Metadata } from 'next';
import { AppShell } from '../../components/AppShell';
import { LiveGridBoard } from '../../components/grid/LiveGrid';

export const metadata: Metadata = { title: 'Live Grid' };

export default function LiveGridPage() {
  return <AppShell><LiveGridBoard /></AppShell>;
}
