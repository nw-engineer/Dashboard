FROM nikolaik/python-nodejs:latest

RUN apt-get update && apt-get install -y vim curl git python3 python3-dev python3-pip procps sqlite3

RUN npm install -g create-react-app
RUN pip3 install uvicorn fastapi
