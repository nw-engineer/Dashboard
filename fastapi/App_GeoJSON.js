import React, { useState, useEffect } from "react";
import { MapContainer, TileLayer, GeoJSON, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import './App.css';

const API_URL = "http://10.2.0.204:8000";

const App = () => {
  const [geojsonData, setGeojsonData] = useState(null);
  const [prefData, setPrefData] = useState({});
  const [selectedPref, setSelectedPref] = useState("");
  const [inputCount, setInputCount] = useState("");

  useEffect(() => {
    fetch("/japan.geojson")
      .then((res) => res.json())
      .then((data) => setGeojsonData(data));
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/prefectures`)
      .then((res) => res.json())
      .then((data) => {
        const mappedData = data.reduce((acc, item) => {
          acc[item.name] = item.count;
          return acc;
        }, {});
        setPrefData(mappedData);
      });
  }, []);

  const getColor = (count) => {
    if (count > 100) return "red";
    if (count > 75) return "orange";
    if (count > 50) return "yellow";
    if (count > 25) return "lightyellow";
    if (count > 10) return "blue";
    if (count > 0) return "lightblue";
    return "white";
  };

  const onEachFeature = (feature, layer) => {
    const count = prefData[feature.properties.nam_ja] || 0;
    layer.setStyle({
      fillColor: getColor(count),
      fillOpacity: 0.7,
      color: "black",
      weight: 1,
    });

    layer.bindTooltip(feature.properties.nam_ja, {
      permanent: true,
      direction: "center",
      className: "custom-tooltip",
    });
  };

  const handleSubmit = () => {
    fetch(`${API_URL}/prefectures`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: selectedPref, count: parseInt(inputCount, 10) || 0 }),
    })
      .then((res) => res.json())
      .then(() => {
        setPrefData((prev) => ({
          ...prev,
          [selectedPref]: parseInt(inputCount, 10) || 0,
        }));
        setInputCount("");
      });
  };

  if (!geojsonData) return <p>Loading...</p>;

  return (
    <div>
      <h1>地図アプリ</h1>
      <div style={{ display: "flex", marginBottom: "10px" }}>
        <select
          value={selectedPref}
          onChange={(e) => setSelectedPref(e.target.value)}
        >
          <option value="">都道府県を選択</option>
          {geojsonData.features.map((feature) => (
            <option key={feature.properties.nam_ja} value={feature.properties.nam_ja}>
              {feature.properties.nam_ja}
            </option>
          ))}
        </select>
        <input
          type="number"
          value={inputCount}
          placeholder="件数を入力"
          onChange={(e) => setInputCount(e.target.value)}
          disabled={!selectedPref}
        />
        <button onClick={handleSubmit} disabled={!selectedPref || !inputCount}>
          保存
        </button>
      </div>
      <MapContainer
        style={{ height: "500px", width: "100%" }}
        center={[35.6895, 139.6917]}
        zoom={5}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <GeoJSON data={geojsonData} onEachFeature={onEachFeature} />
      </MapContainer>
    </div>
  );
};

export default App;
