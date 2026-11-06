import { initializeApp } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyBKKZTrVlz-IdW2VhBh6ce9u939vwndidE",
  authDomain: "borrowbuddy-439d4.firebaseapp.com",
  projectId: "borrowbuddy-439d4",
  storageBucket: "borrowbuddy-439d4.firebasestorage.app",
  messagingSenderId: "772206260910",
  appId: "1:772206260910:web:efca826241c73ac722f33f",
  measurementId: "G-2QJTZ57F72"
};

const app = initializeApp(firebaseConfig);

export const auth = getAuth(app);
export const db = getFirestore(app);
