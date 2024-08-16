import React, { useState, useEffect } from 'react';
import { CssBaseline, Container, Button, FormControl, InputLabel, Select, MenuItem, IconButton, Modal, Box, Typography, TextField } from '@mui/material';
import GridLayout from 'react-grid-layout';
import { Line, Bar, Pie } from 'react-chartjs-2';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import axios from 'axios';
import { SketchPicker } from 'react-color';

import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend
);

const Dashboard = () => {
  const [widgets, setWidgets] = useState([]);
  const [chartType, setChartType] = useState('line');
  const [apiEndpoint, setApiEndpoint] = useState('');
  const [backgroundColor, setBackgroundColor] = useState('rgba(75,192,192,0.4)');
  const [borderColor, setBorderColor] = useState('rgba(75,192,192,1)');
  const [widgetTitle, setWidgetTitle] = useState('Sample Chart');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingWidget, setEditingWidget] = useState(null);

  useEffect(() => {
    axios.get('http://10.2.0.50:4004/api/load_dashboard')
      .then(response => {
        setWidgets(response.data.widgets);
      })
      .catch(error => {
        console.error("There was an error loading the dashboard state!", error);
      });
  }, []);

  const saveDashboard = () => {
    const dashboardState = {
      widgets: widgets
    };
    axios.post('http://10.2.0.50:4004/api/save_dashboard', dashboardState)
      .then(response => {
        console.log(response.data.message);
      })
      .catch(error => {
        console.error("There was an error saving the dashboard state!", error);
      });
  };

  const handleOpen = () => setModalOpen(true);
  const handleClose = () => setModalOpen(false);

  const handleBackgroundColorChange = (color) => {
    setBackgroundColor(color.hex);
  };

  const handleBorderColorChange = (color) => {
    setBorderColor(color.hex);
  };

  const fetchData = () => {
    axios.get(apiEndpoint)
      .then(response => {
        const data = response.data;
        data.datasets[0].backgroundColor = backgroundColor;
        data.datasets[0].borderColor = borderColor;

        if (editingWidget) {
          updateWidget(editingWidget.id, data);
        } else {
          addWidget(data);
        }

        handleClose();
      })
      .catch(error => {
        console.error("There was an error fetching the data!", error);
      });
  };

  const addWidget = (chartData) => {
    const newWidget = {
      id: Date.now().toString(),
      type: chartType,
      x: (widgets.length % 3) * 4,
      y: Math.floor(widgets.length / 3) * 2,
      w: 4,
      h: 4,
      data: chartData,
      title: widgetTitle,
      backgroundColor: backgroundColor,
      borderColor: borderColor,
      apiEndpoint: apiEndpoint,
    };
    setWidgets([...widgets, newWidget]);
  };

  const updateWidget = (id, chartData) => {
    setWidgets(prevWidgets => prevWidgets.map(widget => 
      widget.id === id
        ? { ...widget, data: chartData, title: widgetTitle, backgroundColor, borderColor, apiEndpoint }
        : widget
    ));
    setEditingWidget(null);
  };

  const removeWidget = (id) => {
    setWidgets(prevItems => prevItems.filter(item => item.id !== id));
  };

  const editWidget = (widget) => {
    setChartType(widget.type);
    setApiEndpoint(widget.apiEndpoint || '');
    setBackgroundColor(widget.backgroundColor);
    setBorderColor(widget.borderColor);
    setWidgetTitle(widget.title);
    setEditingWidget(widget);
    setModalOpen(true);
  };

  const layout = widgets.map(item => ({
    i: item.id,
    x: item.x,
    y: item.y,
    w: item.w,
    h: item.h,
  }));

  return (
    <Container maxWidth="lg">
      <CssBaseline />
      <Button variant="contained" color="primary" onClick={handleOpen} sx={{ marginTop: 2, marginRight: 2 }}>
        グラフを追加
      </Button>
      <Button variant="contained" color="primary" onClick={saveDashboard} sx={{ marginTop: 2 }}>
        ダッシュボードを保存
      </Button>

      <Modal
        open={modalOpen}
        onClose={handleClose}
        aria-labelledby="modal-modal-title"
        aria-describedby="modal-modal-description"
      >
        <Box sx={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: 400, bgcolor: 'background.paper', border: '2px solid #000', boxShadow: 24, p: 4 }}>
          <Typography id="modal-modal-title" variant="h6" component="h2">
            グラフを{editingWidget ? '編集' : '追加'}
          </Typography>
          <FormControl fullWidth sx={{ marginTop: 2 }}>
            <TextField
              label="APIエンドポイント"
              variant="outlined"
              value={apiEndpoint}
              onChange={(e) => setApiEndpoint(e.target.value)}
            />
          </FormControl>
          <FormControl fullWidth sx={{ marginTop: 2 }}>
            <InputLabel id="chart-type-label">グラフの種類</InputLabel>
            <Select
              labelId="chart-type-label"
              value={chartType}
              onChange={(e) => setChartType(e.target.value)}
            >
              <MenuItem value="line">ラインチャート</MenuItem>
              <MenuItem value="bar">バーチャート</MenuItem>
              <MenuItem value="pie">円グラフ</MenuItem>
            </Select>
          </FormControl>
          <FormControl fullWidth sx={{ marginTop: 2 }}>
            <TextField
              label="ウィジェットのタイトル"
              variant="outlined"
              value={widgetTitle}
              onChange={(e) => setWidgetTitle(e.target.value)}
            />
          </FormControl>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
            <Box sx={{ width: '45%' }}>
              <Typography variant="body1">背景色</Typography>
              <SketchPicker
                color={backgroundColor}
                onChangeComplete={handleBackgroundColorChange}
                width="100%"
              />
            </Box>
            <Box sx={{ width: '45%' }}>
              <Typography variant="body1">枠線色</Typography>
              <SketchPicker
                color={borderColor}
                onChangeComplete={handleBorderColorChange}
                width="100%"
              />
            </Box>
          </Box>
          <Button variant="contained" color="primary" onClick={fetchData} sx={{ marginTop: 2 }}>
            OK
          </Button>
        </Box>
      </Modal>

      <GridLayout
        className="layout"
        layout={layout}
        cols={12}
        rowHeight={30}
        width={1200}
        compactType="vertical"
        preventCollision={false}
      >
        {widgets.map(widget => (
          <div key={widget.id} data-grid={{ i: widget.id, x: widget.x, y: widget.y, w: widget.w, h: widget.h }}>
            <Typography variant="h6" gutterBottom>
              {widget.title}
              <IconButton onClick={() => removeWidget(widget.id)} size="small" sx={{ marginLeft: 2 }}>
                <DeleteIcon fontSize="small" />
              </IconButton>
              <IconButton onClick={() => editWidget(widget)} size="small" sx={{ marginLeft: 1 }}>
                <EditIcon fontSize="small" />
              </IconButton>
            </Typography>
            {widget.type === 'line' && <Line data={widget.data} />}
            {widget.type === 'bar' && <Bar data={widget.data} />}
            {widget.type === 'pie' && <Pie data={widget.data} />}
          </div>
        ))}
      </GridLayout>
    </Container>
  );
};

export default Dashboard;
