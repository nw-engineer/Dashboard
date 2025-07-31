import React, { useEffect, useState } from "react";
import "./App.css";

const API_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

function App() {
  const [apps, setApps] = useState([]);
  const [selectedApp, setSelectedApp] = useState(null);
  const [formData, setFormData] = useState({ name: "", url: "", description: "" });

  const [ratingValue, setRatingValue] = useState(5);
  const [ratingComment, setRatingComment] = useState("");

  useEffect(() => {
    fetchApps();
  }, []);

  const fetchApps = async () => {
    const res = await fetch(`${API_URL}/apps`);
    const data = await res.json();
    setApps(data);
  };

  const fetchAppDetail = async (id) => {
    const res = await fetch(`${API_URL}/apps/${id}`);
    const data = await res.json();
    setSelectedApp(data);
  };

  const handleAppFormChange = (e) => {
    const { name, value } = e.target;
    setFormData((f) => ({ ...f, [name]: value }));
  };

  const handleAppSubmit = async (e) => {
    e.preventDefault();
    const res = await fetch(`${API_URL}/apps`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData),
    });
    if (res.ok) {
      await fetchApps();
      setFormData({ name: "", url: "", description: "" });
    }
  };

  const handleRatingSubmit = async (e) => {
    e.preventDefault();
    const res = await fetch(`${API_URL}/ratings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        app_id: selectedApp.id,
        rating: ratingValue,
        comment: ratingComment,
      }),
    });
    if (res.ok) {
      await fetchAppDetail(selectedApp.id);
      setRatingComment("");
      setRatingValue(5);
    }
  };

  return (
    <div className="App">
      <h1>フリーソフト申告ダッシュボード</h1>

      <div className="dashboard">
        {/* 左カラム：登録フォーム */}
        <div className="sidebar">
          <form onSubmit={handleAppSubmit} className="form">
            <h2>新規アプリ登録</h2>
            <input name="name" placeholder="アプリ名" value={formData.name} onChange={handleAppFormChange} required />
            <input name="url" placeholder="URL" value={formData.url} onChange={handleAppFormChange} required />
            <textarea name="description" placeholder="用途" value={formData.description} onChange={handleAppFormChange} required />
            <button type="submit">登録</button>
          </form>
        </div>

        {/* 右カラム：アプリ一覧 */}
        <div className="main">
          <table>
            <thead>
              <tr>
                <th>アプリ名</th>
                <th>用途</th>
                <th>AIスコア</th>
                <th>ユーザー評価</th>
                <th>総合</th>
                <th>判定</th>
                <th>詳細</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((app) => (
                <tr key={app.id}>
                  <td>{app.name}</td>
                  <td>{app.description}</td>
                  <td>{app.ai_risk_score}</td>
                  <td>{app.average_rating}</td>
                  <td>{app.composite_score}</td>
                  <td>{app.safety_label}</td>
                  <td><button className="btn-close" onClick={() => fetchAppDetail(app.id)}>詳細</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 詳細ポップアップ */}
      {selectedApp && (
        <div className="modal">
          <div className="modal-content">
            <h2>{selectedApp.name}</h2>
            <p>URL: <a href={selectedApp.url} target="_blank" rel="noreferrer">{selectedApp.url}</a></p>
            <p>用途: {selectedApp.description}</p>
            <p>AIリスクスコア: {selectedApp.risk_score}</p>
            <p>統合スコア: {selectedApp.composite_score}</p>

            <h4>ユーザー評価:</h4>
            <ul>
              {selectedApp.ratings.map((r, i) => (
                <li key={i}>★{r.rating}: {r.comment}</li>
              ))}
            </ul>

	    <div className="risk-report">
              <strong>AIリスクレポート:</strong>
	      <pre className="risk-report-box">
	        {selectedApp.risk_report?.replace(/^\n+/, '')}
	      </pre>
            </div>

            <form onSubmit={handleRatingSubmit} className="form">
              <h3>このアプリを評価する</h3>
              <div className="stars">
                {[1, 2, 3, 4, 5].map((val) => (
                  <span key={val} className={`star ${val <= ratingValue ? "active" : ""}`} onClick={() => setRatingValue(val)}>
                    ★
                  </span>
                ))}
              </div>
              <textarea placeholder="コメントを入力" value={ratingComment} onChange={(e) => setRatingComment(e.target.value)} />
              <button type="submit">評価を送信</button>
            </form>

            <button className="btn-close" onClick={() => setSelectedApp(null)}>閉じる</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;

