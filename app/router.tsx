import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "../components/AppLayout";
import Login from "../routes/Login";
import CaseList from "../routes/CaseList";
import CaseDashboard from "../routes/CaseDashboard";
import Claims from "../routes/Claims";
import Conflicts from "../routes/Conflicts";
import Timeline from "../routes/Timeline";
import Provenance from "../routes/Provenance";
import Reports from "../routes/Reports";
import Audit from "../routes/Audit";
import Documents from "../routes/Documents";
import ChainOfCustody from "../routes/ChainOfCustody";
import PrivilegeReview from "../routes/PrivilegeReview";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <Login />,
  },
  {
    path: "/app",
    element: <AppLayout />,
    children: [
      {
        // /app → /app/cases
        index: true,
        element: <Navigate to="cases" replace />,
      },
      {
        path: "cases",
        element: <CaseList />,
      },
      {
        path: "cases/:slug",
        element: <CaseDashboard />,
      },
      {
        path: "cases/:slug/claims",
        element: <Claims />,
      },
      {
        path: "cases/:slug/conflicts",
        element: <Conflicts />,
      },
      {
        path: "cases/:slug/timeline",
        element: <Timeline />,
      },
      {
        path: "cases/:slug/provenance",
        element: <Provenance />,
      },
      {
        path: "cases/:slug/chain-of-custody",
        element: <ChainOfCustody />,
      },
      {
        path: "cases/:slug/privilege",
        element: <PrivilegeReview />,
      },
      {
        path: "cases/:slug/reports",
        element: <Reports />,
      },
      {
        path: "cases/:slug/audit",
        element: <Audit />,
      },
      {
        path: "audit",
        element: <Navigate to="cases" replace />,
      },
      {
        path: "documents",
        element: <Documents />,
      },
    ],
  },
  {
    path: "/",
    element: <Navigate to="/app/cases" replace />,
  },
  {
    path: "*",
    element: <Navigate to="/app/cases" replace />,
  },
]);
