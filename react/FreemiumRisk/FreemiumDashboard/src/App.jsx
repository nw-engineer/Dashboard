import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";

const API_URL = import.meta.env.VITE_API_URL || "http://10.2.0.204:8000";

export default function Dashboard() {
  const [apps, setApps] = useState([]);
  const [selectedApp, setSelectedApp] = useState(null);

  useEffect(() => {
    fetch(`${API_URL}/apps`)
      .then((res) => res.json())
      .then((data) => setApps(data));
  }, []);

  const fetchAppDetail = async (id) => {
    const res = await fetch(`${API_URL}/apps/${id}`);
    const data = await res.json();
    setSelectedApp(data);
  };

  return (
    <div className="p-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {apps.map((app) => (
        <Card key={app.id} className="hover:shadow-xl transition-shadow">
          <CardContent className="p-4">
            <h2 className="text-lg font-semibold">{app.name}</h2>
            <p className="text-sm text-muted-foreground line-clamp-2 mb-2">
              {app.description}
            </p>
            <p className="text-sm">AIリスクスコア: {app.ai_risk_score}</p>
            <p className="text-sm">ユーザー評価: {app.average_rating}</p>
            <p className="text-sm font-bold">総合スコア: {app.composite_score}</p>
            <p className="text-sm">{app.safety_label}</p>
            <Dialog>
              <DialogTrigger asChild>
                <Button
                  className="mt-3 text-sm"
                  variant="outline"
                  onClick={() => fetchAppDetail(app.id)}
                >
                  詳細を見る
                </Button>
              </DialogTrigger>
              <DialogContent>
                {selectedApp && (
                  <div>
                    <h3 className="text-lg font-semibold mb-2">{selectedApp.name}</h3>
                    <p className="text-sm mb-2">URL: <a href={selectedApp.url} className="text-blue-600 underline" target="_blank" rel="noreferrer">{selectedApp.url}</a></p>
                    <p className="text-sm mb-2">用途: {selectedApp.description}</p>
                    <p className="text-sm mb-2">AIスコア: {selectedApp.risk_score}</p>
                    <p className="text-sm mb-2">統合スコア: {selectedApp.composite_score}</p>
                    <p className="text-sm mb-2">評価コメント:</p>
                    <ul className="list-disc list-inside text-sm text-muted-foreground">
                      {selectedApp.ratings.map((r, idx) => (
                        <li key={idx}>★{r.rating}: {r.comment}</li>
                      ))}
                    </ul>
                    <div className="mt-4 text-sm whitespace-pre-wrap border p-2 bg-muted">
                      <strong>AIリスクレポート:</strong>
                      <p>{selectedApp.risk_report}</p>
                    </div>
                  </div>
                )}
              </DialogContent>
            </Dialog>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

